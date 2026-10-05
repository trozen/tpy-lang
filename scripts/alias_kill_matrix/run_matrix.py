"""Acceptance matrix runner for Optional-field narrowing kills.

Usage (see README.md):
  uv run python scripts/alias_kill_matrix/run_matrix.py --label master [--repo TREE]
      [--only a,b] [--no-truth] [--no-cost]        # results_<label>.json
  uv run python scripts/alias_kill_matrix/run_matrix.py --report      # RESULTS.md
  uv run python scripts/alias_kill_matrix/run_matrix.py --check FILE  # one file (a generated row or any program)

It only spawns subprocesses.  The compiler of `--repo` (default: the checkout
this script lives in) runs as `python -m tpyc.cli` with PYTHONPATH=<repo>,
inside the uv environment of `--env-repo` (default: <repo> when it has a
.venv, else this checkout), so the stdlib (`lib/tpy`) comes from the same
tree.

The rows are generated under build/ per run from gen_shapes.py and
gen_cost.py and never committed. Each shape carries header comments:
  # GROUP: inline | pointer | unmodelled | meet | everyday | view | cost
  # EXPECTED: CHECKED | UNCHECKED | VIEW | COMPILES | <=2x
                                              (what a complete analysis gives)
  # ACCEPTED: CHECKED | UNCHECKED | REJECT -- why  (the shipped rule's verdict, when
                                               it differs: a BUGS.md slug or a
                                               design decision)
  # VERDICT: local (default) | bounds | deref | view | compiles
  # NOTE: free text (may repeat)
The subject read is the one line ending `# SUBJECT`, spelled `y = <read>`;
the module must declare no other `y`.  Verdict: the C++ declaration of `y`
is std::optional<...> -> CHECKED; initialized from `(*...)` -> UNCHECKED.
For VERDICT bounds the subject is `y = xs[i]`: a checked subscript renders
through a checking helper, an elided one as a raw `[...]`.
Truth: the shape runs under CPython; every `print("Y", y)` line is collected:
any `Y None` (or a TypeError/AttributeError/IndexError) -> None-reaches, else stays-value.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BUILD = HERE / "build"
CHECKOUT = HERE.parents[1]
TIMEOUT = 60
# The cost baseline: the front end on a program with nothing to analyze.
TRIVIAL = "def main() -> None:\n    print(1)\n\n\nmain()\n"


def meta(path: Path) -> dict:
    m: dict = {"group": "?", "expected": "?", "accepted": None, "why": "",
               "verdict": "local", "notes": []}
    for line in path.read_text().splitlines():
        mm = re.match(r"#\s*(GROUP|EXPECTED|ACCEPTED|VERDICT|NOTE):\s*(.*)", line)
        if not mm:
            continue
        key, val = mm.group(1).lower(), mm.group(2).strip()
        if key == "note":
            m["notes"].append(val)
        elif key == "accepted":
            got, _, why = val.partition(" -- ")
            m["accepted"], m["why"] = got.strip(), why.strip()
        else:
            m[key] = val
    return m


def env_repo_for(repo: Path, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    if (repo / ".venv").is_dir():
        return repo
    return CHECKOUT


def run(cmd: list[str], env_extra: dict, cwd: Path, timeout: int) -> tuple[int | None, str, str, float]:
    env = dict(os.environ)
    env.update(env_extra)
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr, time.monotonic() - t0
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        err = e.stderr.decode() if isinstance(e.stderr, bytes) else (e.stderr or "")
        return None, out, err, time.monotonic() - t0


def tpyc_cmd(env_repo: Path) -> list[str]:
    return ["uv", "run", "--no-sync", "--project", str(env_repo), "python", "-m", "tpyc.cli"]


def sections(dump: str, mod: str) -> str:
    keep = {f"include/{mod}.hpp", f"include/{mod}_inl.hpp", f"src/{mod}.cpp"}
    out, on = [], False
    for line in dump.splitlines():
        mm = re.match(r"// === (.*) ===$", line)
        if mm:
            on = mm.group(1) in keep
            continue
        if on:
            out.append(line)
    return "\n".join(out)


DECL = re.compile(r"^\s*(?P<type>(?:const\s+)?[A-Za-z_:][\w:<>,\s\*&]*?)\s+y\s*(?:=\s*(?P<init>.*?))?;\s*$")
ASSIGN = re.compile(r"^\s*y\s*=\s*(?P<init>.*?);\s*$")


def classify_local(code: str) -> tuple[str, str]:
    decls = []
    for line in code.splitlines():
        mm = DECL.match(line)
        if mm and not mm.group("type").strip().startswith(("return", "auto&")):
            decls.append((mm.group("type").strip(), (mm.group("init") or "").strip(), line.strip()))
    if not decls:
        return "NO-DECL", ""
    if len(decls) > 1:
        return "AMBIGUOUS", " | ".join(d[2] for d in decls)
    typ, init, line = decls[0]
    if not init:
        for l2 in code.splitlines():
            am = ASSIGN.match(l2)
            if am:
                init = am.group("init")
                line = line + " ... " + l2.strip()
                break
    if "optional" in typ:
        return "CHECKED", line
    if "deref_optional_check" in init:
        return "CHECKED", line
    if init.startswith("(*") or "(*" in init:
        return "UNCHECKED", line
    return "UNKNOWN", line


def classify_bounds(code: str) -> tuple[str, str]:
    for line in code.splitlines():
        if re.search(r"\by\s*=", line) and not line.strip().startswith("//"):
            if re.search(r"check|getitem|at\(", line):
                return "CHECKED", line.strip()
            if "[" in line:
                return "UNCHECKED", line.strip()
            return "UNKNOWN", line.strip()
    return "NO-DECL", ""


def classify_deref(code: str) -> tuple[str, str]:
    for line in code.splitlines():
        if re.search(r"\sy\s*=", line) and not line.strip().startswith("//"):
            if "deref_check" in line or "deref_optional_check" in line:
                return "CHECKED", line.strip()
            if "->" in line:
                return "UNCHECKED", line.strip()
            return "UNKNOWN", line.strip()
    return "NO-DECL", ""


def classify_compiles(code: str) -> tuple[str, str]:
    return "COMPILES", ""


def classify_view(code: str) -> tuple[str, str]:
    for line in code.splitlines():
        mm = DECL.match(line)
        if mm:
            typ = mm.group("type")
            if "string_view" in typ or "View" in typ:
                return "VIEW", line.strip()
            if "string" in typ.lower() or "Bytes" in typ:
                return "OWNED", line.strip()
            return "UNKNOWN", line.strip()
    return "NO-DECL", ""


def first_error(text: str) -> str:
    lines = text.splitlines()
    for line in lines:
        if re.search(r"\berror\b", line, re.I):
            return line.strip()[:300]
    for line in reversed(lines):
        if line.strip():
            return line.strip()[:300]
    return ""


def compile_verdict(shape: Path, repo: Path, env_repo: Path, m: dict) -> dict:
    work = Path(tempfile.mkdtemp(prefix="cmp_", dir=BUILD))
    try:
        src = work / shape.name
        shutil.copy(shape, src)
        rc, out, err, dt = run(tpyc_cmd(env_repo) + ["--dump-code", src.name], {"PYTHONPATH": str(repo)}, work, TIMEOUT)
        res = {"seconds": round(dt, 2)}
        if rc is None:
            res.update(verdict="TIMEOUT", detail="")
            return res
        if rc != 0:
            kind = "CRASH" if "Traceback" in err else "REJECT"
            res.update(verdict=kind, detail=first_error(err))
            return res
        code = sections(out, shape.stem)
        classify = {"bounds": classify_bounds, "deref": classify_deref, "view": classify_view,
                    "compiles": classify_compiles}.get(m["verdict"], classify_local)
        v, d = classify(code)
        res.update(verdict=v, detail=d)
        return res
    finally:
        shutil.rmtree(work, ignore_errors=True)


def truth(shape: Path, repo: Path, env_repo: Path) -> dict:
    work = Path(tempfile.mkdtemp(prefix="cpy_", dir=BUILD))
    try:
        src = work / shape.name
        shutil.copy(shape, src)
        rc, out, err, _ = run(["uv", "run", "--no-sync", "--project", str(env_repo), "python", src.name],
                              {"PYTHONPATH": str(repo / "lib" / "cpy")}, work, TIMEOUT)
        ys = [l[2:] for l in out.splitlines() if l.startswith("Y ")]
        if rc is None:
            return {"truth": "TRUTH-TIMEOUT", "ys": ys}
        if rc != 0:
            last = err.strip().splitlines()[-1] if err.strip() else ""
            if last.startswith(("TypeError", "AttributeError", "IndexError")):
                return {"truth": "None-reaches", "ys": ys, "raised": last[:200]}
            return {"truth": "TRUTH-ERROR", "ys": ys, "raised": last[:200]}
        if not ys:
            return {"truth": "NO-Y", "ys": ys}
        reaches = any(y.split()[:1] == ["None"] for y in ys)
        return {"truth": "None-reaches" if reaches else "stays-value", "ys": ys}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def cost_seconds(shape: Path | None, repo: Path, env_repo: Path) -> tuple[float | None, str]:
    work = Path(tempfile.mkdtemp(prefix="cost_", dir=BUILD))
    try:
        if shape is None:
            src = work / "trivial.py"
            src.write_text(TRIVIAL)
        else:
            src = work / shape.name
            shutil.copy(shape, src)
        rc, _out, err, dt = run(tpyc_cmd(env_repo) + [src.name, "-o", "out", "--no-bundle-runtime"],
                                {"PYTHONPATH": str(repo)}, work, TIMEOUT)
        if rc is None:
            return None, "TIMEOUT"
        if rc != 0:
            return round(dt, 2), ("CRASH: " if "Traceback" in err else "REJECT: ") + first_error(err)
        return round(dt, 2), ""
    finally:
        shutil.rmtree(work, ignore_errors=True)


def shape_sha(shape: Path) -> str:
    return hashlib.sha256(shape.read_bytes()).hexdigest()[:12]


def commit_of(repo: Path) -> str:
    p = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo, capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else "?"


def measure(label: str, repo: Path, env_repo: Path, only: set[str] | None, do_truth: bool, do_cost: bool) -> None:
    BUILD.mkdir(exist_ok=True)
    out_path = HERE / f"results_{label}.json"
    sys.path.insert(0, str(HERE))
    import gen_cost
    import gen_shapes
    # Every row is generated per run; the generators are the source.
    shapes = gen_shapes.generate(BUILD / "shapes") + gen_cost.generate(BUILD / "cost")
    # rows not re-measured this run (--only, --no-cost) keep their last result
    results: dict = {}
    if out_path.exists():
        live = {s.stem for s in shapes}
        results = {k: v for k, v in json.loads(out_path.read_text()).get("rows", {}).items() if k in live}
    baseline = None
    for shape in shapes:
        name = shape.stem
        if only and name not in only:
            continue
        m = meta(shape)
        row: dict = {"group": m["group"], "expected": m["expected"],
                     "accepted": m["accepted"], "why": m["why"], "notes": m["notes"],
                     "sha": shape_sha(shape)}
        if m["group"] == "cost":
            if not do_cost:
                continue
            if baseline is None:
                samples = [cost_seconds(None, repo, env_repo)[0] for _ in range(3)]
                baseline = min(s for s in samples if s is not None)
            secs, diag = cost_seconds(shape, repo, env_repo)
            row.update(seconds=secs, baseline=baseline,
                       verdict="TIMEOUT" if secs is None else f"+{secs - baseline:.2f}s",
                       ratio=None if secs is None else round(secs / baseline, 2), detail=diag)
        else:
            row.update(compile_verdict(shape, repo, env_repo, m))
            if do_truth:
                row.update(truth(shape, repo, env_repo))
        results[name] = row
        print(f"{name:40} {row.get('verdict')!s:12} {row.get('truth', '')!s:14} {row.get('detail', '')[:90]}", flush=True)
    meta_out = {"label": label, "repo": str(repo), "commit": commit_of(repo),
                "baseline_seconds": baseline, "rows": results}
    if baseline is None and out_path.exists():
        meta_out["baseline_seconds"] = json.loads(out_path.read_text()).get("baseline_seconds")
    out_path.write_text(json.dumps(meta_out, indent=1, sort_keys=True))
    shutil.rmtree(BUILD, ignore_errors=True)


def check(path: Path, repo: Path, env_repo: Path) -> None:
    BUILD.mkdir(exist_ok=True)
    m = meta(path)
    print(compile_verdict(path, repo, env_repo, m))
    print(truth(path, repo, env_repo))
    shutil.rmtree(BUILD, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label")
    ap.add_argument("--repo", default=str(CHECKOUT))
    ap.add_argument("--env-repo")
    ap.add_argument("--only")
    ap.add_argument("--no-truth", action="store_true")
    ap.add_argument("--no-cost", action="store_true")
    ap.add_argument("--check")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.report:
        sys.path.insert(0, str(HERE))
        import make_report
        make_report.main()
        return
    repo = Path(a.repo).resolve()
    env_repo = env_repo_for(repo, a.env_repo)
    if a.check:
        check(Path(a.check).resolve(), repo, env_repo)
        return
    if not a.label:
        ap.error("--label is required")
    only = set(a.only.split(",")) if a.only else None
    measure(a.label, repo, env_repo, only, not a.no_truth, not a.no_cost)


if __name__ == "__main__":
    main()
