"""MIR coverage measurement: how many bodies lower to MIR, finish every MIR
analysis, show a conflict, and get a storage certificate. See README.md.

    uv run python scripts/mir_coverage/run.py [--corpus tests|stdlib|examples|all]
        [--cases CASE ...] [-j N] [--out DIR] [--examples PATH]
        [--compare PREV_DIR] [--report-only] [--fraction F] [--min-per-group M]
"""
import argparse
import json
import hashlib
import os
import signal
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CASES = REPO / "tests" / "cases"
STDLIB_CASE = "harness/stdlib_render"
EXAMPLE_DIRS = ("basics", "tplib", "programs", "landing", "shedskin")
MAX_JOBS_WITHOUT_FLAG = 4

sys.path.insert(0, str(HERE))


def _load_opts(case_dir: Path) -> dict:
    # Same merge as driver.load_opts; duplicated so building the job list does
    # not import the driver (which installs the sema hooks).
    layers = []
    cur = case_dir
    while True:
        layers.append(cur / "options.json")
        if cur == CASES or cur == cur.parent:
            break
        cur = cur.parent
    out: dict = {}
    for layer in reversed(layers):
        if layer.is_file():
            for k, v in json.loads(layer.read_text()).items():
                if k == "dsl_opts":
                    out.setdefault("dsl_opts", {}).update(v)
                else:
                    out[k] = v
    return out


def _entry(case: Path) -> Path | None:
    src = case / "src"
    main = src / "main.py"
    if main.is_file():
        return main
    cands = sorted(p for p in src.iterdir() if p.is_file() and p.name.startswith("main."))
    return cands[0] if cands else None


def eligible_cases() -> list[str]:
    """Compiling cases with a snapshot: the population the sample is drawn from."""
    out = []
    for src in sorted(CASES.rglob("src")):
        case = src.parent
        if not (case / "expected").is_dir() or case.name.startswith(("error_", "panic_")):
            continue
        rel = str(case.relative_to(CASES))
        if rel == STDLIB_CASE or _entry(case) is None:
            continue
        out.append(rel)
    return out


def sample_cases(fraction: float, minimum: int) -> dict:
    """The fixed denominator: a case is sampled when the hash of its name falls
    under `fraction`, and a group smaller than `minimum` is taken whole.

    Keyed on the name so membership is stable as the corpus grows: a new case
    joins with the same probability and moves nothing else, which a seeded
    random draw would not give."""
    groups = defaultdict(list)
    for rel in eligible_cases():
        groups[rel.split("/")[0]].append(rel)
    cases: list[str] = []
    counts = {}
    for g in sorted(groups):
        members = sorted(groups[g])
        if len(members) <= minimum:
            chosen = members
        else:
            ranked = sorted(members, key=_name_hash)
            k = max(minimum, sum(1 for m in members if _name_hash(m) < fraction))
            chosen = sorted(ranked[:k])
        cases.extend(chosen)
        counts[g] = [len(members), len(chosen)]
    return {"rule": {"fraction": fraction, "min_per_group": minimum,
                     "population": "tests/cases compiling cases with expected/, minus harness/stdlib_render"},
            "groups": counts, "cases": cases}


def _name_hash(rel: str) -> float:
    return int(hashlib.sha1(rel.encode()).hexdigest()[:8], 16) / 2**32


def _test_job(rel: str) -> dict | None:
    case = CASES / rel
    entry = _entry(case) if (case / "src").is_dir() else None
    if entry is None:
        return None
    return {"corpus": "tests", "program": rel, "src": str(entry), "opts": _load_opts(case), "mode": "user"}


def build_jobs(args, sample: dict | None) -> tuple[list[dict], list[str]]:
    jobs: list[dict] = []
    notes: list[str] = []
    corpora = ("tests", "stdlib", "examples") if args.corpus == "all" else (args.corpus,)
    if "tests" in corpora:
        names = args.cases if args.cases else sample["cases"]
        missing = []
        for rel in names:
            job = _test_job(rel)
            if job is None:
                missing.append(rel)
            else:
                jobs.append(job)
        if missing:
            # The denominator is fixed: a vanished case is reported, not dropped silently.
            notes.append(f"{len(missing)} requested cases not found under tests/cases: {missing[:10]}")
    if "stdlib" in corpora:
        case = CASES / STDLIB_CASE
        jobs.append({"corpus": "stdlib", "program": STDLIB_CASE, "src": str(_entry(case)),
                     "opts": _load_opts(case), "mode": "stdlib"})
    if "examples" in corpora:
        root = args.examples or os.environ.get("TPY_EXAMPLES")
        if not root or not Path(root).is_dir():
            msg = "examples corpus skipped: pass --examples PATH or set TPY_EXAMPLES to a tpy-examples checkout"
            if args.corpus == "examples":
                sys.exit(msg)
            notes.append(msg)
        else:
            root_path = Path(root).resolve()
            for sub in EXAMPLE_DIRS:
                d = root_path / sub
                if not d.is_dir():
                    continue
                if sub in ("programs", "shedskin"):
                    entries = sorted(x / f"{x.name}.py" for x in d.iterdir() if (x / f"{x.name}.py").is_file())
                else:
                    entries = sorted(d.glob("*.py"))
                for p in entries:
                    jobs.append({"corpus": "examples", "program": str(p.relative_to(root_path)), "src": str(p),
                                 "opts": {}, "mode": "user"})
    return jobs, notes


def run_jobs(jobs: list[dict], out_dir: Path, workers: int, timeout: int) -> None:
    """One forked child per job: isolates sema's global state, memory and crashes."""
    import driver  # noqa: F401  -- import (and hook) once in the parent; children inherit it
    parts = out_dir / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    # Largest first so the long stdlib compile does not become the tail.
    order = sorted(range(len(jobs)), key=lambda i: jobs[i]["corpus"] != "stdlib")
    pending = [i for i in order if not (parts / f"{i}.json").exists()]
    running: dict[int, tuple[int, float]] = {}
    done = len(jobs) - len(pending)
    t0 = time.monotonic()
    sys.stdout.flush()
    sys.stderr.flush()
    while pending or running:
        while pending and len(running) < workers:
            i = pending.pop(0)
            pid = os.fork()
            if pid == 0:
                _child(jobs[i], parts / f"{i}.json", timeout)
            running[pid] = (i, time.monotonic())
        pid, wstatus = os.wait()
        if pid not in running:
            continue
        i, started = running.pop(pid)
        part = parts / f"{i}.json"
        if not part.exists():
            job = jobs[i]
            sig = os.WTERMSIG(wstatus) if os.WIFSIGNALED(wstatus) else None
            why = ("timeout" if sig == signal.SIGALRM else
                   f"killed by signal {sig}" if sig else f"exit status {os.WEXITSTATUS(wstatus)}")
            part.write_text(json.dumps({"corpus": job["corpus"], "program": job["program"], "mode": job["mode"],
                                        "status": "exception", "error": f"worker died: {why}", "bodies": [],
                                        "diags": [], "t": round(time.monotonic() - started, 2)}))
        done += 1
        if done % 50 == 0 or done == len(jobs):
            print(f"  {done}/{len(jobs)} programs, {time.monotonic() - t0:.0f}s", flush=True)
    with (out_dir / "programs.jsonl").open("w") as fh:
        for i in range(len(jobs)):
            fh.write((parts / f"{i}.json").read_text().strip() + "\n")


def _child(job: dict, part: Path, timeout: int) -> None:
    code = 1
    try:
        # SIGALRM's default action ends the child; the parent reports the timeout.
        signal.alarm(timeout)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        import driver
        result = driver.run_job(job)
        tmp = part.with_suffix(".tmp")
        tmp.write_text(json.dumps(result))
        tmp.rename(part)
        code = 0
    finally:
        os._exit(code)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", choices=("tests", "stdlib", "examples", "all"), default="all")
    ap.add_argument("--cases", nargs="+", metavar="CASE",
                    help="measure these tests/cases paths instead of the sample (e.g. records/basic)")
    ap.add_argument("-j", "--jobs", type=int, default=3, help="parallel programs (default 3)")
    ap.add_argument("--allow-more-jobs", action="store_true",
                    help=f"permit -j above {MAX_JOBS_WITHOUT_FLAG} (the machine is shared)")
    ap.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "agents" / "mir-coverage" / "latest")
    ap.add_argument("--examples", help="tpy-examples checkout (default: $TPY_EXAMPLES)")
    ap.add_argument("--compare", type=Path, metavar="PREV_DIR", help="a previous --out dir (its bodies.csv)")
    ap.add_argument("--timeout", type=int, default=900, help="per-program seconds (default 900)")
    ap.add_argument("--resume", action="store_true", help="keep finished programs in --out and run the rest")
    ap.add_argument("--report-only", action="store_true", help="re-analyze an existing --out dir")
    ap.add_argument("--no-diag-scan", action="store_true", help="skip the expected/diag.txt family scan")
    ap.add_argument("--fraction", type=float, default=0.4, help="sampled share of each tests/cases group")
    ap.add_argument("--min-per-group", type=int, default=8, help="a group this small is taken whole")
    args = ap.parse_args(argv)

    if args.jobs < 1 or (args.jobs > MAX_JOBS_WITHOUT_FLAG and not args.allow_more_jobs):
        ap.error(f"-j must be 1..{MAX_JOBS_WITHOUT_FLAG} (pass --allow-more-jobs to go higher)")
    sample = sample_cases(args.fraction, args.min_per_group)

    out_dir = args.out
    if not args.report_only:
        jobs, notes = build_jobs(args, sample)
        for n in notes:
            print(f"note: {n}")
        if out_dir.exists() and not args.resume:
            for stale in (out_dir / "parts").glob("*.json") if (out_dir / "parts").is_dir() else ():
                stale.unlink()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "jobs.json").write_text(json.dumps(jobs))
        print(f"measuring {len(jobs)} programs with -j {args.jobs} into {out_dir}", flush=True)
        t0 = time.monotonic()
        run_jobs(jobs, out_dir, args.jobs, args.timeout)
        elapsed = time.monotonic() - t0
        (out_dir / "run.json").write_text(json.dumps({
            "jobs": len(jobs), "workers": args.jobs, "elapsed_s": round(elapsed, 1), "notes": notes,
            "corpus": args.corpus, "sample": sample["rule"] if not args.cases else None}, indent=1) + "\n")
        print(f"done in {elapsed:.0f}s", flush=True)

    import analyze
    text = analyze.report(out_dir, sample if not args.cases else None, args.compare,
                          None if args.no_diag_scan or args.cases or args.corpus not in ("tests", "all") else CASES)
    print(text, end="")
    print(f"\nwrote {out_dir / 'report.txt'}, bodies.csv, summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
