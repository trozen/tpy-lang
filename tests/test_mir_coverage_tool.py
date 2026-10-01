"""Smoke test for `scripts/mir_coverage/run.py` over three tiny cases.

The tool wraps sema classes at runtime, so it runs in a subprocess: importing
it into a pytest worker would leak the wrappers into every later test there.
Front-end only, no C++ toolchain. Lives beside the case suite because the
sdist ships `tpyc/` without `scripts/`, hence the skip.
"""
import csv
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_RUN = _REPO / "scripts" / "mir_coverage" / "run.py"

pytestmark = pytest.mark.skipif(not _RUN.exists(), reason="scripts/mir_coverage is not part of this checkout")

# A constructor and a free function that lower, a cross-module call, and a
# dataclass whose methods do not.
_CASES = ["none_safety/concrete_assignment_proves", "imports/import_basic", "records/dataclass_astuple"]


def test_run_reports_four_count(tmp_path: Path) -> None:
    out = tmp_path / "out"
    proc = subprocess.run([sys.executable, str(_RUN), "--corpus", "tests", "--cases", *_CASES,
                           "-j", "1", "--out", str(out)],
                          capture_output=True, text=True, timeout=300, cwd=_REPO)
    assert proc.returncode == 0, proc.stdout + proc.stderr

    programs = [json.loads(line) for line in (out / "programs.jsonl").read_text().splitlines()]
    assert sorted(p["program"] for p in programs) == sorted(_CASES)
    assert all(p["status"] == "ok" for p in programs), [p.get("error") for p in programs]

    with (out / "bodies.csv").open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert {r["position"] for r in rows} >= {"constructor", "free_function", "module_init"}
    lowered = [r for r in rows if r["lowered"] == "True"]
    assert lowered, "no body lowered"
    # Every lowered body carries an analysis and a storage verdict; nothing
    # without proof obligations may pass as certified.
    assert all(r["analyses"] in ("complete", "incomplete", "error") for r in lowered)
    assert all(r["storage"] for r in lowered)
    assert not any(r["certified"] == "True" and r["storage"] != "certified" for r in rows)
    assert all(r["reason_cat"] for r in rows if r["lowered"] == "False" and r["status"] != "no_body")

    summary = json.loads((out / "summary.json").read_text())
    counts = summary["four_count"]["tests"]
    assert counts["lowered"] == len([r for r in lowered if r["status"] != "no_body"])
    assert counts["certified"] + counts["no_proof"] <= counts["lowered"]
    # The exit fact is reported per lowered body and counted beside the four.
    assert all(r["exceptional_exits"] in ("True", "False") for r in lowered)
    assert counts["exceptional"] == sum(r["exceptional_exits"] == "True" for r in lowered)

    # Comparing a run with itself moves no blocker.
    proc = subprocess.run([sys.executable, str(_RUN), "--corpus", "tests", "--cases", *_CASES,
                           "--out", str(out), "--report-only", "--compare", str(out)],
                          capture_output=True, text=True, timeout=120, cwd=_REPO)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "0 bodies changed first blocker" in (out / "report.txt").read_text()


def test_job_limit_needs_flag() -> None:
    proc = subprocess.run([sys.executable, str(_RUN), "--corpus", "stdlib", "-j", "5"],
                          capture_output=True, text=True, timeout=60, cwd=_REPO)
    assert proc.returncode != 0
    assert "--allow-more-jobs" in proc.stderr
