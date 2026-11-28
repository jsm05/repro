"""SWT-bench Verified instances: records -> Instance, images, and per-instance cleanup.

Records are SWE-bench Verified dataset rows for the SWT-bench Verified instances,
exported to a JSONL file in Actions (D-M6-12). `patch` is the real fix, `test_patch` the
real tests; both are sealed, as are `hints_text` and the fail-to-pass names. The agent's
view carries only the issue text, repo, base commit and the test framework.

Images: SWT-bench builds `exec.eval.*` instance images (D-E0-1). Before any sandbox
starts, Repro derives a hardened image from it once per instance: `.git` replaced by a
fresh single-commit repo at the base commit (no later commit, remote or reflog left)
and the harness's eval commands (locale setup) applied. Every sandbox, the agent's and
the gate's, starts from that hardened image with the network off.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from repro.datasets import swt_harness as swt
from repro.gate.scope import parse_diff, repair_patch
from repro.models import AgentView, Instance, SealedView, TestCmdSpec
from repro.sandbox import docker as dk
from repro.sandbox.base import Sandbox

log = logging.getLogger(__name__)

CONDA_ACTIVATE = "source /opt/miniconda3/bin/activate testbed"
CONDA_PYTHON = "/opt/miniconda3/envs/testbed/bin/python"
HARDEN_SCRIPT = r"""set -e
cd /testbed
rm -rf .git
git init -q
git add -A
git -c user.name=repro -c user.email=repro@localhost commit -q --no-verify -m "base commit"
git gc -q --prune=now 2>/dev/null || true
rm -f /root/pre_state.patch
"""
_DJANGO_NAME = re.compile(r"^(?P<method>\w+) \((?P<dotted>[\w.]+)\)")


@dataclass(frozen=True)
class HarnessSpec:
    """How to run tests for one instance, taken from the SWT-bench harness."""

    repo: str
    version: str
    framework: str
    test_cmd: str
    parser: str
    pre_commands: tuple[str, ...] = ()


def strip_patch_tags(patch: str) -> str:
    p = patch
    if p.startswith("<patch>\n"):
        p = p[len("<patch>\n"):]
    if p.endswith("\n</patch>"):
        p = p[: -len("\n</patch>")]
    return p if p.endswith("\n") else p + "\n"


def _names(value: Any) -> list[str]:
    if isinstance(value, str):
        return [str(x) for x in json.loads(value)] if value.strip() else []
    return [str(x) for x in (value or [])]


def django_id(name: str) -> str | None:
    """"test_x (app.tests.Cls)" or "test_x (app.tests.Cls.test_x)" -> our test id."""
    m = _DJANGO_NAME.match(name.strip())
    if not m:
        return None
    method, parts = m.group("method"), m.group("dotted").split(".")
    if parts and parts[-1] == method:
        parts = parts[:-1]
    if len(parts) < 2:
        return None
    cls, module = parts[-1], parts[:-1]
    return f"tests/{'/'.join(module)}.py::{cls}::{method}"


def django_label(test_id: str) -> str:
    """Our test id -> a runtests.py label ("tests/a/b.py::Cls::m" -> "a.b.Cls.m")."""
    path, _, rest = test_id.partition("::")
    module = swt.directives("django/django", [path])[0]
    return ".".join([module, *[p for p in rest.split("::") if p]])


def test_files(test_patch: str) -> dict[str, str]:
    """Test files the gold test patch touches, with the code it adds (for name lookup)."""
    out: dict[str, str] = {}
    for f in parse_diff(test_patch):
        out[f.path] = "\n".join(line[1:] for h in f.hunks for line in h.lines
                                if line.startswith("+"))
    return out


def to_test_id(framework: str, name: str, test_patch: str) -> str | None:
    if framework == "django":
        tid = django_id(name)
        if tid is not None:
            return tid
        # Django prints a docstring's first line instead of the name when a test has one,
        # and SWE-bench keeps that ("#24155 - Tests ordering of imports."). Keep it as is,
        # tied to the test file whose added lines contain it (D-M6-14).
        files = test_files(test_patch)
        for path, added in files.items():
            if name.strip() and name.strip() in added:
                return f"{path}::{name.strip()}"
        # The docstring may be in unchanged lines: tie it to the first test file; the
        # runner matches it by its text in the log and the file's source.
        for path in files:
            if name.strip():
                return f"{path}::{name.strip()}"
        return None
    if framework == "sympy":
        for path, added in test_files(test_patch).items():
            if re.search(rf"^def {re.escape(name)}\s*\(", added, re.M):
                return f"{path}::{name}"
        for path in test_files(test_patch):
            return f"{path}::{name}"
        return None
    return name  # pytest node ids are already ours


def record_to_instance(rec: dict[str, Any]) -> tuple[Instance, HarnessSpec, list[str]]:
    """Map one dataset record. Returns the instance, its harness spec, and any
    fail-to-pass names that could not be turned into test ids (kept out, reported)."""
    repo, version = str(rec["repo"]), str(rec["version"])
    fw = swt.framework(repo)
    test_patch = repair_patch(str(rec["test_patch"]))
    f2p, p2p, unmapped = [], [], []
    for name in _names(rec.get("FAIL_TO_PASS")):
        tid = to_test_id(fw, name, test_patch)
        if tid:
            f2p.append(tid)
        else:
            unmapped.append(name)
    for name in _names(rec.get("PASS_TO_PASS")):
        tid = to_test_id(fw, name, test_patch)
        if tid:
            p2p.append(tid)
    public = AgentView(
        instance_id=str(rec["instance_id"]), repo=repo, base_commit=str(rec["base_commit"]),
        issue_text=str(rec["problem_statement"]),
        test_cmd_spec=TestCmdSpec(framework=fw, test_dirs=[]),
    )
    sealed = SealedView(
        gold_fix_patch=repair_patch(strip_patch_tags(str(rec["patch"]))),
        gold_test_patch=test_patch,
        fail_to_pass=f2p, pass_to_pass=p2p, hints_text=str(rec.get("hints_text") or ""),
    )
    harness = HarnessSpec(repo=repo, version=version, framework=fw,
                          test_cmd=swt.test_command(repo, version),
                          parser=swt.parser_name(repo),
                          pre_commands=tuple(swt.eval_commands(repo, version)))
    return Instance(public=public, sealed=sealed), harness, unmapped


def gold_test_ids(instance: Instance) -> list[str]:
    """Test ids for the gold test patch: its fail-to-pass tests (our id format)."""
    return list(instance.sealed.fail_to_pass)


def hardened_tag(instance_id: str) -> str:
    return f"repro-hardened:{instance_id.lower().replace('/', '_')}"


class ContainerRepo:
    """RepoFiles over a sandbox (for BM25 retrieval in the baselines)."""

    def __init__(self, make: Callable[[], Sandbox]):
        self._make = make
        self._sb: Sandbox | None = None
        self._files: list[str] | None = None

    def _box(self) -> Sandbox:
        if self._sb is None:
            self._sb = self._make()
        return self._sb

    def list_files(self) -> list[str]:
        if self._files is None:
            # exec's output is capped (16 KB, middle cut), which drops files in big repos,
            # so the listing goes through a scratch file instead (D-M7-9).
            box = self._box()
            scratch = ".repro/files.txt"
            box.put_file(scratch, "")
            box.exec(f"git ls-files > {scratch}", timeout_s=120)
            listing = box.get_file(scratch).decode("utf-8", errors="replace")
            box.delete_file(scratch)
            self._files = sorted(x for x in listing.splitlines() if x)
        return self._files

    def read(self, path: str) -> str:
        return self._box().get_file(path).decode("utf-8", errors="replace")

    def exists(self, path: str) -> bool:
        return path in set(self.list_files())

    def close(self) -> None:
        if self._sb is not None:
            self._sb.close()
            self._sb = None


def harden(image: str, tag: str, run_id: str = "adhoc", script: str = HARDEN_SCRIPT) -> str:
    """Commit `image` with .git replaced by one fresh commit (DESIGN 5.2 leak hardening).
    Runs once per instance with the same locked-down flags; nothing needs the network."""
    import uuid

    name = f"repro-harden-{uuid.uuid4().hex[:10]}"
    dk._docker(*dk.run_argv(image, name, dk.DockerSettings(run_id=run_id)), check=True)
    try:
        dk._docker("exec", name, "/bin/bash", "-c", script, check=True, timeout=1800)
        dk._docker("commit", name, tag, check=True, timeout=1800)
    finally:
        dk._docker("rm", "--force", name, timeout=120)
    return tag


class SWTBenchDataset:
    """Instances from a records JSONL; images built on demand and removed afterwards."""

    def __init__(self, records_path: str | Path, run_id: str = "adhoc", *,
                 build: bool = True, cleanup: bool = True, keep_env_below_gb: float = 12.0):
        self.records: dict[str, dict[str, Any]] = {}
        for line in Path(records_path).read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                self.records[str(rec["instance_id"])] = rec
        self.run_id = run_id
        self.build = build
        self.cleanup = cleanup
        self.keep_env_below_gb = keep_env_below_gb
        self._open: dict[str, list[Any]] = {}
        self._images: dict[str, dict[str, str]] = {}

    def ids(self) -> list[str]:
        return sorted(self.records)

    def settings(self, harness: HarnessSpec) -> dk.DockerSettings:
        pre = "; ".join([CONDA_ACTIVATE, *harness.pre_commands])
        return dk.DockerSettings(run_id=self.run_id, python=CONDA_PYTHON, activate=pre)

    def image(self, instance_id: str) -> str:
        """The hardened image for an instance, building and hardening it if needed."""
        tag = hardened_tag(instance_id)
        if dk.image_exists(tag):
            return tag
        rec = self.records[instance_id]
        keys = swt.image_keys([rec], build=self.build)[instance_id]
        self._images[instance_id] = keys
        if not dk.image_exists(keys["image"]):
            raise RuntimeError(f"instance image {keys['image']} was not built")
        harden(keys["image"], tag, self.run_id)
        return tag

    def load(self, instance_id: str) -> Any:
        from repro.eval.execute import LoadedInstance

        instance, harness, unmapped = record_to_instance(self.records[instance_id])
        if unmapped:
            log.warning("%s: %d fail-to-pass names not mapped to test ids: %s", instance_id,
                        len(unmapped), unmapped[:3])
        factory = dk.docker_factory(self.image(instance_id), self.settings(harness),
                                    harness=harness)
        repo = ContainerRepo(factory)
        self._open[instance_id] = [repo]
        return LoadedInstance(instance, repo, factory)

    def release(self, instance_id: str) -> float:
        """Close what the unit opened, remove its images, prune; returns free disk (GB)."""
        for obj in self._open.pop(instance_id, []):
            obj.close()
        if self.cleanup:
            keys = self._images.pop(instance_id, {})
            dk.remove_images([hardened_tag(instance_id), *([keys["image"]] if keys else [])])
            dk.prune()
            free = dk.disk_free_gb()
            if free < self.keep_env_below_gb and keys:
                dk.remove_images([keys["env_image"]])
                dk.prune()
        free = dk.disk_free_gb()
        log.info("disk free after %s: %.1f GB", instance_id, free)
        return free


def export_records(dataset_rows: Sequence[dict[str, Any]], ids: Sequence[str],
                   out: Path) -> int:
    wanted = set(ids)
    rows = [r for r in dataset_rows if r["instance_id"] in wanted]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    return len(rows)
