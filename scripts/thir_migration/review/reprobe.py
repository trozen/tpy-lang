"""Re-run every BREAKS probe of the review bins on the current tree, front end
only, and classify each row: REJECTS (with the live reject tag), COMPILES,
FRONTEND_ERROR, CRASH. This is the queue's re-measurement instrument: the bins
were binned once (2026-09-01) and rows close as lowering arms land, so run
this before choosing a batch rather than reading counts off the bins.

    uv run python scripts/thir_migration/review/reprobe.py OUT_DIR [-j N]

Writes OUT_DIR/results.json (one record per BREAKS row, joined with its bin
row) and prints the tallies by outcome, lowering file and reject tag. A probe
whose first lines name `--default-int X` runs with that flag.
"""
import argparse
import collections
import glob
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
BINS = os.path.join(REPO, "scripts", "thir_migration", "review", "bins_*.json")

REJECT_RE = re.compile(
    r"^(?P<file>[^\s:]+):(?P<line>\d+): error: (?P<where>.*?): this construct "
    r"is not yet supported by C\+\+ code generation \((?P<tag>[^)]*)\)",
    re.M)
ERR_RE = re.compile(r"^.*?: error: .*$", re.M)
ARGS_RE = re.compile(r"^#.*?default-int[ =]+(\w+)", re.M)


def probe_args(path):
    with open(path, "r", errors="replace") as fh:
        head = "".join(fh.readlines()[:5])
    m = ARGS_RE.search(head)
    return (["--default-int", m.group(1)], m.group(1)) if m else ([], None)


def run_one(outroot, key, path):
    extra, dflt = probe_args(os.path.join(REPO, path))
    cmd = ["uv", "run", "tpyc", path, "-o", os.path.join(outroot, key)] + extra
    try:
        p = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                           timeout=300)
        out, rc = p.stdout + p.stderr, p.returncode
    except subprocess.TimeoutExpired:
        out, rc = "<TIMEOUT>", -9
    rec = {"probe": path, "rc": rc, "default_int": dflt}
    m = REJECT_RE.search(out)
    if m:
        rec.update(kind="REJECTS", tag=m.group("tag"),
                   reject_line=int(m.group("line")), msg=m.group(0))
    elif "Traceback (most recent call last)" in out or "Internal error" in out:
        rec.update(kind="CRASH", msg=out.strip()[-1500:])
    elif rc == 0:
        rec.update(kind="COMPILES")
    else:
        e = ERR_RE.search(out)
        rec.update(kind="FRONTEND_ERROR",
                   msg=(e.group(0) if e else out.strip()[-600:]))
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("-j", type=int, default=8)
    args = ap.parse_args()
    outroot = os.path.join(args.out_dir, "emit")
    os.makedirs(outroot, exist_ok=True)

    rows = []
    for f in sorted(glob.glob(BINS)):
        for r in json.load(open(f)):
            if r.get("bin") == "BREAKS":
                r["slice"] = os.path.basename(f)[5:-5]
                rows.append(r)
    probes = sorted({r["probe"] for r in rows if r.get("probe")})
    keys = {p: "p%04d" % i for i, p in enumerate(probes)}
    results = {}
    with ThreadPoolExecutor(max_workers=args.j) as ex:
        futs = {p: ex.submit(run_one, outroot, keys[p], p) for p in probes}
        for i, (p, fut) in enumerate(futs.items()):
            results[p] = fut.result()
            if (i + 1) % 50 == 0:
                print("done", i + 1, "/", len(probes), file=sys.stderr)

    joined = []
    for r in rows:
        res = results.get(r.get("probe"))
        joined.append({"file": r["file"], "line": r["line"], "func": r["func"],
                       "reason": r["reason"], "slice": r["slice"],
                       "probe": r.get("probe"),
                       "kind": res["kind"] if res else "NO_PROBE",
                       "tag": res.get("tag") if res else None,
                       "msg": res.get("msg") if res else None})
    with open(os.path.join(args.out_dir, "results.json"), "w") as fh:
        json.dump({"n_breaks_rows": len(rows), "n_probes": len(probes),
                   "rows": joined}, fh, indent=1)

    kinds = collections.Counter(j["kind"] for j in joined)
    print("BREAKS rows:", len(rows), "distinct probes:", len(probes))
    for k, n in kinds.most_common():
        print("  %-15s %d" % (k, n))
    rej = [j for j in joined if j["kind"] == "REJECTS"]
    print("\nrejecting rows by lowering file:")
    for f, n in collections.Counter(j["file"] for j in rej).most_common():
        print("  %4d  %s" % (n, f))
    print("\nrejecting rows by live tag (top 25 of %d):"
          % len({j["tag"] for j in rej}))
    for t, n in collections.Counter(j["tag"] for j in rej).most_common(25):
        print("  %4d  %s" % (n, t))
    now = [j for j in joined if j["kind"] == "COMPILES"]
    if now:
        print("\nrows that COMPILE now (close them in the bins):")
        for j in now:
            print("  %s:%d %s  %s" % (j["file"], j["line"], j["func"], j["probe"]))
    bad = [j for j in joined if j["kind"] in ("CRASH", "FRONTEND_ERROR")]
    if bad:
        print("\nCRASH / FRONTEND_ERROR rows (contract violations or stale probes):")
        for j in bad:
            print("  %s %s:%d  %s" % (j["kind"], j["file"], j["line"], j["probe"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
