"""SWT-bench loader, harness commands and parsers, canary, image cleanup (TEST_SPEC M6)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from repro.models import TestStatus

HERE = Path(__file__).resolve().parent
LOGS = HERE.parent / "fixtures" / "swt_logs"
HAVE_SWT = (Path(os.environ.get("REPRO_SWT_BENCH", "/tmp/swt-bench")) / "src" /
            "constants.py").is_file()
needs_swt = pytest.mark.skipif(not HAVE_SWT, reason="SWT-bench checkout not available "
                               "(set REPRO_SWT_BENCH; CI clones it)")

FIX = """diff --git a/django/contrib/auth/validators.py b/django/contrib/auth/validators.py
--- a/django/contrib/auth/validators.py
+++ b/django/contrib/auth/validators.py
@@ -7,4 +7,4 @@
 @deconstructible
 class ASCIIUsernameValidator(validators.RegexValidator):
-    regex = r'^[\\w.@+-]+$'
+    regex = r'^[\\w.@+-]+\\Z'
     message = _(
"""
TEST_PATCH = (
    "diff --git a/tests/auth_tests/test_validators.py b/tests/auth_tests/test_validators.py\n"
    """--- a/tests/auth_tests/test_validators.py
+++ b/tests/auth_tests/test_validators.py
@@ -10,2 +10,6 @@ class UsernameValidatorsTests(SimpleTestCase):
     def test_unicode_validator(self):
         pass
+
+    def test_ascii_validator_trailing_newline(self):
+        v = validators.ASCIIUsernameValidator()
+        self.assertRaises(ValidationError, v, 'trailingnewline\\n')
""")
DJANGO_REC: dict[str, Any] = {
    "repo": "django/django", "instance_id": "django__django-11099",
    "base_commit": "d26b2424437dabeeca94d7900b37d2df4410da0c",
    "patch": "<patch>\n" + FIX.rstrip("\n") + "\n</patch>", "test_patch": TEST_PATCH,
    "problem_statement": "UsernameValidator allows trailing newline in usernames.",
    "hints_text": "The fix is to replace the dollar sign with backslash Z in both regexes so "
                  "trailing newlines are rejected.",
    "created_at": "2019-03-20", "version": "3.0",
    "FAIL_TO_PASS": json.dumps(["test_ascii_validator_trailing_newline "
                                "(auth_tests.test_validators.UsernameValidatorsTests)"]),
    "PASS_TO_PASS": json.dumps(["test_unicode_validator "
                                "(auth_tests.test_validators.UsernameValidatorsTests)"]),
    "environment_setup_commit": "419a78300f7cd27611196e1e464d50fd0385ff27",
}
F2P_ID = ("tests/auth_tests/test_validators.py::UsernameValidatorsTests::"
          "test_ascii_validator_trailing_newline")


@needs_swt
def test_t6_4_loader_separates_public_and_sealed() -> None:
    """T6.4: a record maps to AgentView/SealedView; hints and fail-to-pass names sealed."""
    from repro.datasets.swtbench import hardened_tag, record_to_instance

    inst, harness, unmapped = record_to_instance(dict(DJANGO_REC))
    pub = inst.public.model_dump_json()
    assert inst.public.issue_text.startswith("UsernameValidator allows trailing newline")
    assert inst.public.test_cmd_spec.framework == "django"
    for sealed_bit in ("backslash Z", "test_ascii_validator_trailing_newline", r"+\Z'",
                       "diff --git"):
        assert sealed_bit not in pub, sealed_bit
    s = inst.sealed
    assert s.fail_to_pass == [F2P_ID] and unmapped == []
    assert s.pass_to_pass == [
        "tests/auth_tests/test_validators.py::UsernameValidatorsTests::test_unicode_validator"]
    assert s.hints_text.startswith("The fix is")
    assert s.gold_fix_patch.startswith("diff --git a/django/") and "<patch>" not in \
        s.gold_fix_patch
    assert harness.test_cmd.startswith("./tests/runtests.py --verbosity 2")
    assert harness.parser == "parse_log_django"
    assert any("LANG=en_US.UTF-8" in c for c in harness.pre_commands)
    assert hardened_tag("django__django-11099") == "repro-hardened:django__django-11099"


def test_django_id_and_label_round_trip() -> None:
    """T6.4: Django test names map to our ids and back to runtests labels."""
    from repro.datasets.swtbench import django_id, django_label

    assert django_id("test_x (app.tests.Cls)") == "tests/app/tests.py::Cls::test_x"
    assert django_id("test_x (app.tests.Cls.test_x)") == "tests/app/tests.py::Cls::test_x"
    assert django_id("Regression test for #123") is None
    assert django_label("tests/app/tests.py::Cls::test_x") == "app.tests.Cls.test_x"


@needs_swt
def test_t6_5_commands_and_parsers_from_the_harness() -> None:
    """T6.5: test command and parser come from the SWT-bench specs, pytest repo and Django."""
    from repro.datasets import swt_harness as swt

    assert swt.framework("psf/requests") == "pytest"
    assert swt.test_command("psf/requests", "2.0").startswith("pytest")
    assert swt.parser_name("psf/requests") == "parse_log_pytest_options"
    parsed = swt.log_parser("psf/requests")((LOGS / "pytest_requests.log").read_text())
    assert parsed["tests/test_requests.py::test_b"] == "FAILED"
    assert parsed["tests/test_requests.py::test_a"] == "PASSED"
    assert swt.framework("django/django") == "django"
    assert swt.test_command("django/django", "3.0") == (
        "./tests/runtests.py --verbosity 2 --settings=test_sqlite --parallel 1")
    assert swt.directives("django/django", ["tests/auth_tests/test_validators.py"]) == [
        "auth_tests.test_validators"]
    assert swt.framework("sympy/sympy") == "sympy"
    assert swt.parser_name("sympy/sympy") == "parse_log_sympy"


@needs_swt
def test_t6_5_django_log_to_outcomes() -> None:
    """T6.5: a recorded Django log gives statuses, exception types and repo frames."""
    from repro.datasets import swt_harness as swt
    from repro.runner.harness_runner import parse_harness_run

    log = (LOGS / "django.log").read_text()
    base = "tests/auth_tests/test_validators.py::UsernameValidatorsTests::"
    ids = [base + "test_ascii_validator", base + "test_raises", base + "test_unicode_validator",
           base + "test_missing"]
    calls = {"auth_tests.test_validators.UsernameValidatorsTests.test_raises":
             ["django/contrib/auth/validators.py:__call__"]}
    out = parse_harness_run("django", swt.log_parser("django/django")(log), log, ids,
                            "/testbed", calls)
    a, r, u, m = (out[i] for i in ids)
    assert a.status is TestStatus.FAILED and a.failure is not None
    assert a.failure.exc_type == "AssertionError" and a.failure.phase == "call"
    assert r.status is TestStatus.FAILED and r.failure is not None  # unittest ERROR = raised
    assert r.failure.exc_type == "TypeError" and r.failure.message == "bad input"
    assert [f.path for f in r.failure.frames] == ["tests/auth_tests/test_validators.py",
                                                  "django/contrib/auth/validators.py"]
    assert r.repo_calls == ["django/contrib/auth/validators.py:__call__"]
    assert u.status is TestStatus.PASSED
    assert m.status is TestStatus.ERROR and m.note == "not in test log"


@needs_swt
def test_t6_5_sympy_log_to_outcomes() -> None:
    """T6.5: a recorded sympy bin/test log gives statuses and failure details."""
    from repro.datasets import swt_harness as swt
    from repro.runner.harness_runner import parse_harness_run

    log = (LOGS / "sympy.log").read_text()
    ids = [f"sympy/core/tests/test_basic.py::test_{x}" for x in "abc"]
    out = parse_harness_run("sympy", swt.log_parser("sympy/sympy")(log), log, ids,
                            "/testbed", {"*": ["sympy/core/basic.py:g"]})
    assert out[ids[0]].status is TestStatus.PASSED
    assert out[ids[1]].status is TestStatus.FAILED
    assert out[ids[1]].failure.exc_type == "AssertionError"  # type: ignore[union-attr]
    c = out[ids[2]]
    assert c.status is TestStatus.FAILED and c.failure is not None
    assert c.failure.exc_type == "ValueError" and c.failure.frames[-1].path == \
        "sympy/core/basic.py"
    assert c.repo_calls == ["sympy/core/basic.py:g"]


def test_trace_file_merge() -> None:
    """T6.5: tracer lines from several processes merge."""
    from repro.runner.harness_runner import read_calls

    text = '{"*": ["a.py:f"]}\n{"*": ["b.py:g"], "t1": ["a.py:f"]}\n'
    assert read_calls(text) == {"*": ["a.py:f", "b.py:g"], "t1": ["a.py:f"]}


def test_t6_6_canary_finds_planted_fix_hunk(tmp_path: Path) -> None:
    """T6.6: the canary scanner finds a planted fix hunk in a trace, and nothing in a clean one."""
    from repro.eval.canary import markers, scan_run
    from repro.models import SealedView

    sealed = SealedView(gold_fix_patch=FIX, gold_test_patch=TEST_PATCH,
                        fail_to_pass=[F2P_ID], pass_to_pass=[],
                        hints_text=DJANGO_REC["hints_text"])
    kinds = {k for k, _ in markers(sealed)}
    assert {"fix_line", "gold_test_name", "fail_to_pass", "hints_text"} <= kinds
    run = tmp_path / "run"
    (run / "traces").mkdir(parents=True)
    clean = {"type": "tool_call", "payload": {"cmd": "grep -rn regex django/contrib"}}
    (run / "traces" / "clean.jsonl").write_text(json.dumps(clean) + "\n")
    leaked = {"type": "tool_call", "payload": {"output": "    regex = r'^[\\w.@+-]+\\Z'"}}
    (run / "traces" / "leak.jsonl").write_text(json.dumps(clean) + "\n" + json.dumps(leaked))
    hits = scan_run(run, {"clean": sealed, "leak": sealed})
    assert [h.kind for h in hits] == ["fix_line"]
    assert hits[0].where.endswith("leak.jsonl")


def test_t6_11_images_removed_and_disk_logged(tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """T6.11: after each instance its images are removed, docker prunes, disk is logged."""
    import repro.datasets.swt_harness as swt
    from repro.datasets.swtbench import SWTBenchDataset, hardened_tag
    from tests.unit.test_docker_sandbox import FAKE, Fake

    fake = Fake(tmp_path)
    monkeypatch.setenv("REPRO_DOCKER", str(FAKE))
    monkeypatch.setenv("FAKE_DOCKER_LOG", str(fake.log))
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(fake.state))
    keys = {"image": "exec.eval.x86_64.aaa.bbb:latest", "env_image": "exec.env.x86_64.aaa:latest",
            "base_image": "exec.base.x86_64:latest"}
    fake.add_images(keys["image"], keys["env_image"])
    monkeypatch.setattr(swt, "image_keys", lambda recs, build=False: {
        r["instance_id"]: keys for r in recs})
    records = tmp_path / "recs.jsonl"
    records.write_text(json.dumps(DJANGO_REC) + "\n")
    ds = SWTBenchDataset(records, "run-x", keep_env_below_gb=1e9)
    tag = ds.image("django__django-11099")
    assert tag == hardened_tag("django__django-11099")
    calls = fake.calls()
    harden_run = next(c for c in calls if c[0] == "run")
    assert "--network" in harden_run and harden_run[harden_run.index("--network") + 1] == "none"
    assert any(c[0] == "exec" and "rm -rf .git" in " ".join(c) for c in calls)
    assert ["commit", harden_run[harden_run.index("--name") + 1], tag] in calls
    free = ds.release("django__django-11099")
    calls = fake.calls()
    assert ["rmi", "--force", tag, keys["image"]] in calls
    assert ["system", "prune", "--force"] in calls
    assert ["rmi", "--force", keys["env_image"]] in calls  # disk below the threshold
    assert free > 0


def test_t6_11_executor_releases_each_unit(tmp_path: Path) -> None:
    """T6.11: the executor releases every unit, logs free disk, and releases after a crash."""
    from repro.config import EvalConfig
    from repro.eval.execute import Executor, FixtureDataset, LLMHandle
    from repro.llm.fake import FakeLLM
    from tests.helpers import FIXTURES_ROOT

    released: list[str] = []

    class Tracked(FixtureDataset):
        def release(self, instance_id: str) -> float:
            released.append(instance_id)
            return 33.3

    cfg = EvalConfig(config_id="x", kind="B0", llm="cassette", gate_timeout_s=30)
    ds = Tracked(str(FIXTURES_ROOT))
    out = Executor(cfg, "r", ds, lambda i: LLMHandle(FakeLLM(["no answer"]))).execute(
        "fixture__f01_off_by_one")
    trace = out.files["traces/fixture__f01_off_by_one.jsonl"]
    assert '"type":"disk"' in trace.replace(" ", "") and "33.3" in trace
    assert released == ["fixture__f01_off_by_one"]

    def boom(i: str) -> LLMHandle:
        raise RuntimeError("worker died")

    with pytest.raises(RuntimeError):
        Executor(cfg, "r", ds, boom).execute("fixture__f02_raises")
    assert released[-1] == "fixture__f02_raises"


@needs_swt
def test_harness_runner_end_to_end_through_the_gate(tmp_path: Path) -> None:
    """T6.5: a unittest repo run the Django way: statuses from the harness parser, failure
    details from the log, repo calls from the tracer, and the gate passes a real repro."""
    from repro.datasets.swtbench import HarnessSpec
    from repro.gate.checks import GateConfig, OnlineGate
    from repro.models import Candidate
    from repro.runner.pytest_runner import run_tests
    from repro.sandbox.local import LocalSandbox

    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "mod.py").write_text("def double(x):\n    return x * 3  # bug\n")
    (repo / "tests" / "test_mod.py").write_text(
        "import unittest\nfrom pkg.mod import double\n\n\n"
        "class ModTests(unittest.TestCase):\n    def test_zero(self):\n"
        "        self.assertEqual(double(0), 0)\n")
    harness = HarnessSpec(repo="django/django", version="3.0", framework="django",
                          test_cmd="cd tests && PYTHONPATH=..:$PYTHONPATH python -m unittest -v",
                          parser="parse_log_django")

    class HarnessBox(LocalSandbox):
        def __init__(self) -> None:
            super().__init__(repo)
            self.harness = harness

    def factory() -> HarnessBox:
        sb = HarnessBox()
        sb.start()
        return sb

    with factory() as sb:
        res = run_tests(sb, ["tests/test_mod.py::ModTests::test_zero"], trace=True)
    o = res.outcomes["tests/test_mod.py::ModTests::test_zero"]
    assert o.status is TestStatus.PASSED and o.repo_calls == ["pkg/mod.py:double"]

    new_test = ("diff --git a/tests/test_bug.py b/tests/test_bug.py\nnew file mode 100644\n"
                "--- /dev/null\n+++ b/tests/test_bug.py\n@@ -0,0 +1,7 @@\n"
                "+import unittest\n+from pkg.mod import double\n+\n+\n"
                "+class BugTests(unittest.TestCase):\n+    def test_double_two(self):\n"
                "+        self.assertEqual(double(2), 4)\n")
    cand = Candidate(diff=new_test, test_ids=["tests/test_bug.py::BugTests::test_double_two"])
    v = OnlineGate(factory, GateConfig(timeout_s=60)).verify(cand)
    assert v.passed, v.reason
    assert "AssertionError" in v.signatures["tests/test_bug.py::BugTests::test_double_two"]


def test_repair_patch_and_form_feeds() -> None:
    """D-M6-12: trimmed trailing blank context is restored; form feeds do not split lines."""
    from repro.gate.candidate import file_diff
    from repro.gate.scope import DiffParseError, parse_diff, repair_patch

    full = ("diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n@@ -1,3 +1,4 @@\n"
            " def f():\n+    x = 1\n     pass\n \n")
    with pytest.raises(DiffParseError, match="middle of a hunk"):
        parse_diff(full.strip())
    assert repair_patch(full.strip()) == full
    assert repair_patch(full) == full  # an intact patch is unchanged
    ff = ("diff --git a/t.py b/t.py\n--- a/t.py\n+++ b/t.py\n@@ -1,2 +1,2 @@\n"
          "-a\x0cb\n+a\x0cc\n x\n")
    assert parse_diff(ff)[0].hunks[0].lines == ["-a\x0cb", "+a\x0cc", " x"]
    d = file_diff("t.py", "a\x0cb\nx\n", "a\x0cc\nx\n")
    assert parse_diff(d)[0].hunks[0].lines[:2] == ["-a\x0cb", "+a\x0cc"]


@needs_swt
def test_loader_repairs_trimmed_patches() -> None:
    """D-M6-12: a record whose patches were stripped still loads with parseable patches."""
    from repro.datasets.swtbench import record_to_instance
    from repro.gate.scope import parse_diff

    rec = dict(DJANGO_REC, test_patch=TEST_PATCH.strip(), patch=FIX.strip())
    inst, _, unmapped = record_to_instance(rec)
    assert parse_diff(inst.sealed.gold_test_patch) and parse_diff(inst.sealed.gold_fix_patch)
    assert inst.sealed.fail_to_pass == [F2P_ID] and unmapped == []


@needs_swt
def test_file_level_requests_expand_to_every_test_in_the_file() -> None:
    """D-M6-13: the oracle asks for whole files; every test found there gets an outcome."""
    from repro.datasets import swt_harness as swt
    from repro.runner.harness_runner import parse_harness_run

    log = (LOGS / "django.log").read_text()
    out = parse_harness_run("django", swt.log_parser("django/django")(log), log,
                            ["tests/auth_tests/test_validators.py"], "/testbed", None)
    base = "tests/auth_tests/test_validators.py::UsernameValidatorsTests::"
    assert {t: o.status for t, o in out.items()} == {
        base + "test_ascii_validator": TestStatus.FAILED,
        base + "test_raises": TestStatus.FAILED,
        base + "test_unicode_validator": TestStatus.PASSED}
    slog = (LOGS / "sympy.log").read_text()
    path = "sympy/core/tests/test_basic.py"
    defs = {path: "def test_a():\n    pass\n\ndef test_b():\n    pass\n\ndef test_c():\n    pass\n"}
    sout = parse_harness_run("sympy", swt.log_parser("sympy/sympy")(slog), slog, [path],
                             "/testbed", None, defs)
    assert sorted(sout) == [f"{path}::test_{x}" for x in "abc"]


def test_oracle_ignores_tests_that_already_failed(tmp_path: Path) -> None:
    """D-M6-13: a test in the touched file that fails before and after (F→F) is not
    collateral; one that passed before and fails after (P→F) is."""
    from repro.models import Candidate, InstanceOutcome
    from repro.oracle.oracle import run_oracle
    from repro.sandbox.local import local_factory

    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "mod.py").write_text("def double(x):\n    return x * 3\n")
    (repo / "tests" / "test_mod.py").write_text(
        "from pkg.mod import double\n\n\ndef test_env_broken():\n    assert False\n\n\n"
        "def test_keeps_three():\n    assert double(1) in (2, 3)\n")
    fix = ("diff --git a/pkg/mod.py b/pkg/mod.py\n--- a/pkg/mod.py\n+++ b/pkg/mod.py\n"
           "@@ -1,2 +1,2 @@\n def double(x):\n-    return x * 3\n+    return x * 2\n")
    add = ("diff --git a/tests/test_mod.py b/tests/test_mod.py\n--- a/tests/test_mod.py\n"
           "+++ b/tests/test_mod.py\n@@ -8,2 +8,6 @@ def test_env_broken():\n"
           " def test_keeps_three():\n     assert double(1) in (2, 3)\n+\n+\n"
           "+def test_double():\n+    assert double(2) == 4\n")
    cand = Candidate(diff=add, test_ids=["tests/test_mod.py::test_double"])
    v = run_oracle(cand, fix, local_factory(repo))
    assert v.outcome is InstanceOutcome.REPRODUCED, v.reason
    assert v.transitions["tests/test_mod.py::test_env_broken"].label == "F→F"
    breaking = fix.replace("x * 2", "x * 2 if x != 1 else 7")
    v2 = run_oracle(cand, breaking, local_factory(repo))
    assert v2.outcome is InstanceOutcome.WRONG_TEST and "O2 failed" in v2.reason


@needs_swt
def test_docstring_named_django_tests() -> None:
    """D-M6-14: a fail-to-pass name that is a docstring maps to its file and matches the log."""
    from repro.datasets.swtbench import record_to_instance
    from repro.runner.harness_runner import _label, parse_harness_run

    patch = TEST_PATCH.replace(
        "+        v = validators.ASCIIUsernameValidator()\n",
        '+        """#24155 - Tests trailing newlines."""\n'
        "+        v = validators.ASCIIUsernameValidator()\n").replace("@@ -10,2 +10,6 @@",
                                                              "@@ -10,2 +10,7 @@")
    rec = dict(DJANGO_REC, test_patch=patch,
               FAIL_TO_PASS=json.dumps(["#24155 - Tests trailing newlines."]))
    inst, _, unmapped = record_to_instance(rec)
    tid = "tests/auth_tests/test_validators.py::#24155 - Tests trailing newlines."
    assert inst.sealed.fail_to_pass == [tid] and unmapped == []
    assert _label("django", tid) == "auth_tests.test_validators"
    log = ("#24155 - Tests trailing newlines. ... FAIL\n\n" + "=" * 70 + "\n"
           "FAIL: #24155 - Tests trailing newlines.\n" + "-" * 70 + "\n"
           "Traceback (most recent call last):\n  File \"/testbed/tests/auth_tests/"
           "test_validators.py\", line 14, in test_x\n    v(1)\nAssertionError: boom\n")
    statuses = {"#24155 - Tests trailing newlines.": "FAILED"}
    defs = {"tests/auth_tests/test_validators.py": patch}
    out = parse_harness_run("django", statuses, log, ["tests/auth_tests/test_validators.py"],
                            "/testbed", None, defs)
    assert out[tid].status is TestStatus.FAILED
    assert out[tid].failure is not None and out[tid].failure.exc_type == "AssertionError"


def test_oracle_also_runs_candidate_tests_outside_touched_files(tmp_path: Path) -> None:
    """D-M6-14: a candidate test id in a file the diff does not touch is still run."""
    from repro.models import Candidate, InstanceOutcome
    from repro.oracle.oracle import run_oracle
    from repro.sandbox.local import local_factory

    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pkg" / "__init__.py").write_text("")
    (repo / "pkg" / "mod.py").write_text("def double(x):\n    return x * 3\n")
    (repo / "tests" / "base.py").write_text("")
    (repo / "tests" / "test_inherit.py").write_text(
        "from tests.base import *  # noqa\n\n\nclass TestKids(Base):\n    pass\n")
    add = ("diff --git a/tests/base.py b/tests/base.py\n--- a/tests/base.py\n"
           "+++ b/tests/base.py\n@@ -0,0 +1,6 @@\n+from pkg.mod import double\n+\n+\n"
           "+class Base:\n+    def test_double(self):\n+        assert double(2) == 4\n")
    fix = ("diff --git a/pkg/mod.py b/pkg/mod.py\n--- a/pkg/mod.py\n+++ b/pkg/mod.py\n"
           "@@ -1,2 +1,2 @@\n def double(x):\n-    return x * 3\n+    return x * 2\n")
    (repo / "tests" / "__init__.py").write_text("")
    cand = Candidate(diff=add, test_ids=["tests/test_inherit.py::TestKids::test_double"])
    v = run_oracle(cand, fix, local_factory(repo))
    assert v.outcome is InstanceOutcome.REPRODUCED, v.reason


@needs_swt
def test_docstring_in_unchanged_lines_maps_to_the_test_file() -> None:
    """D-M6-15: a docstring name that is not in the patch's added lines still maps."""
    from repro.datasets.swtbench import record_to_instance

    rec = dict(DJANGO_REC, FAIL_TO_PASS=json.dumps(["An existing test's docstring."]))
    inst, _, unmapped = record_to_instance(rec)
    assert inst.sealed.fail_to_pass == [
        "tests/auth_tests/test_validators.py::An existing test's docstring."]
    assert unmapped == []


@needs_swt
def test_docstring_statuses_from_the_line_after_the_name() -> None:
    """D-M6-16: Django prints a docstring on the line after the name; both get the status."""
    from repro.runner.harness_runner import django_docstring_statuses, parse_harness_run

    log = ("test_sorted_imports (migrations.test_writer.WriterTests.test_sorted_imports)\n"
           "#24155 - Tests ordering of imports. ... FAIL\n"
           "test_simple (migrations.test_writer.WriterTests.test_simple) ... ok\n")
    st = django_docstring_statuses(log)
    assert st == {"test_sorted_imports (migrations.test_writer.WriterTests.test_sorted_imports)":
                  "FAILED", "#24155 - Tests ordering of imports.": "FAILED"}
    raw = "tests/migrations/test_writer.py::#24155 - Tests ordering of imports."
    out = parse_harness_run("django", st, log, ["tests/migrations/test_writer.py", raw],
                            "/testbed", None, {})
    assert out[raw].status is TestStatus.FAILED
    assert out["tests/migrations/test_writer.py::WriterTests::test_sorted_imports"].status \
        is TestStatus.FAILED
