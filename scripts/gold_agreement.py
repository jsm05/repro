"""Gold agreement on the pilot instances (T6.D8). Runs in docker-tests.yml.

For each instance: build and harden its image, run our oracle on the gold test patch
(its fail-to-pass tests must go F→P), then run the official SWT-bench harness on the
same gold patch with the same images, and compare. Images are removed between
instances. Writes a markdown table and a JSON file; exit 1 if any instance disagrees.

    python scripts/gold_agreement.py eval/samples/pilot_10.txt \
        --records eval/data/swt_records.jsonl --out eval/gold-agreement.md

In Actions each instance runs in its own matrix job (`--only ID`), and a last job merges
the per-instance JSON files (`--merge DIR`) into the report.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from repro.datasets.swt_harness import swt_root  # noqa: E402
from repro.datasets.swtbench import SWTBenchDataset  # noqa: E402
from repro.gate.checks import GateConfig  # noqa: E402
from repro.models import Candidate, InstanceOutcome  # noqa: E402
from repro.oracle.oracle import run_oracle  # noqa: E402
from repro.sandbox.docker import reap  # noqa: E402

DATASET = "nmuendler/SWT-Bench_Verified_bm25_27k_zsp"


def find_resolution(obj: Any, instance: str, key: str = "") -> str | None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            found = find_resolution(v, instance, str(k))
            if found:
                return found
    elif isinstance(obj, list) and instance in obj and "resolved" in key.lower():
        return "unresolved" if "unresolved" in key.lower() else "resolved"
    return None


def official(instance_id: str, run_id: str) -> str:
    root = swt_root()
    proc = subprocess.run(
        [sys.executable, "-m", "src.main", "--dataset_name", DATASET, "--is_swt", "true",
         "--predictions_path", "gold", "--max_workers", "1", "--instance_ids", instance_id,
         "--run_id", run_id, "--cache_level", "instance", "--clean", "false",
         "--compute_coverage", "false", "--timeout", "900"],
        cwd=root, capture_output=True, text=True, timeout=3600, check=False)
    candidates = [p for p in root.rglob("*.json") if run_id in str(p)]
    if not candidates and (root / "evaluation_results").is_dir():
        candidates = sorted((root / "evaluation_results").rglob("*.json"))
    for path in candidates:
        try:
            found = find_resolution(json.loads(path.read_text()), instance_id)
        except (ValueError, OSError):
            continue
        if found:
            return found
    print(proc.stdout[-4000:], proc.stderr[-4000:], sep="\n", flush=True)
    return f"no result (exit {proc.returncode})"


def write_report(rows: list[dict[str, Any]], out: Path, total: int) -> int:
    agree = sum(1 for r in rows if r["agree"])
    lines = ["# Gold agreement on the pilot (T6.D8)", "",
             f"Our oracle vs the official SWT-bench harness on the gold test patches: "
             f"{agree} of {len(rows)} agree ({len(rows)} of {total} instances done).", "",
             "| Instance | Ours | Official | Agree | F→P per fail-to-pass test | Seconds | Note |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        trans = ", ".join(f"{t.split('::')[-1]} {lbl}" for t, lbl in
                          sorted(r.get("transitions", {}).items()))
        note = (r.get("error") or ("" if r["ours"] == "REPRODUCED" else r.get("ours_reason", "")))
        lines.append(f"| `{r['instance_id']}` | {r['ours']} | {r['official'][:40]} | "
                     f"{'yes' if r['agree'] else 'NO'} | {trans} | {r['seconds']} | "
                     f"{str(note).replace('|', '/')[:160]} |")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    out.with_suffix(".json").write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n")
    return agree


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("ids_file")
    ap.add_argument("--records", default="eval/data/swt_records.jsonl")
    ap.add_argument("--out", default="eval/gold-agreement.md")
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds per test run")
    ap.add_argument("--only", help="run just this instance (one matrix job per instance)")
    ap.add_argument("--merge", help="directory of per-instance JSON files: write the report")
    args = ap.parse_args()
    ids = Path(args.ids_file).read_text().split()
    if args.merge:
        rows = []
        for f in sorted(Path(args.merge).rglob("*.json")):
            rows += [r for r in json.loads(f.read_text()) if r.get("instance_id") in ids]
        rows.sort(key=lambda r: ids.index(r["instance_id"]))
        missing = [i for i in ids if i not in {r["instance_id"] for r in rows}]
        for iid in missing:
            rows.append({"instance_id": iid, "ours": "not run", "official": "not run",
                         "agree": False, "seconds": 0, "error": "no result from its job"})
        agree = write_report(rows, Path(args.out), len(ids))
        print(f"agreement: {agree}/{len(ids)}")
        return 0 if agree == len(ids) else 1
    if args.only:
        ids = [args.only]
    ds = SWTBenchDataset(args.records, run_id="gold-agreement")
    reap("gold-agreement")
    out = Path(args.out)
    # Resume: instances already in the JSON report (from an earlier, cut-off run) are kept.
    rows: list[dict[str, Any]] = []
    if out.with_suffix(".json").exists():
        rows = [r for r in json.loads(out.with_suffix(".json").read_text())
                if r["instance_id"] in ids and "error" not in r]
    done = {r["instance_id"] for r in rows}
    for iid in ids:
        if iid in done:
            print(f"{iid}: already in the report, skipped", flush=True)
            continue
        t0 = time.monotonic()
        row: dict[str, Any] = {"instance_id": iid}
        try:
            loaded = ds.load(iid)
            sealed = loaded.instance.sealed
            cand = Candidate(diff=sealed.gold_test_patch, test_ids=sealed.fail_to_pass,
                             origin="gold")
            v = run_oracle(cand, sealed.gold_fix_patch, loaded.factory,
                           GateConfig(timeout_s=args.timeout))
            row["ours"] = v.outcome.value
            row["ours_reason"] = v.reason[:200]
            row["transitions"] = {t: tr.label for t, tr in v.transitions.items()
                                  if t in sealed.fail_to_pass}
            row["official"] = official(iid, f"gold-{iid}")
        except Exception as exc:  # record and move on to the next instance
            row.setdefault("ours", "ERROR")
            row["error"] = f"{type(exc).__name__}: {exc}"[:400]
            row.setdefault("official", "not run")
        finally:
            try:
                row["disk_free_gb"] = round(ds.release(iid), 1)
            except Exception as exc:
                row["cleanup_error"] = str(exc)[:200]
            reap("gold-agreement")
        ours_ok = row["ours"] == InstanceOutcome.REPRODUCED.value
        row["agree"] = ours_ok == (row["official"] == "resolved")
        row["seconds"] = round(time.monotonic() - t0)
        rows.append(row)
        print(json.dumps(row), flush=True)
        write_report(rows, out, len(ids))  # after every instance, so a cut-off run keeps it

    agree = write_report(rows, Path(args.out), len(ids))
    print(f"agreement: {agree}/{len(ids)}")
    return 0 if agree == len(ids) else 1


if __name__ == "__main__":
    if os.environ.get("REPRO_DOCKER") is None:
        os.environ.setdefault("REPRO_DOCKER", "docker")
    sys.exit(main())
