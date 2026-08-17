"""Full raise-site SETS over ALL blocked cases (soles + multis).

The multi-blocker set-cover measurement: a multi-blocker case flips only
when EVERY one of its raise sites clears, so the paying view is the
per-case set of (site, reason) pairs plus a greedy set-cover ranking of
sites by how many cases each round of clearing would flip.

Per-body lowering aborts at the FIRST ThirUnsupported, so later blockers
in the same body stay hidden until the first clears -- the cover is a
lower bound on remaining work, same as the chain-walk reality.

Usage (from the repo root, after probe_corpus.py; LOCAL, writes JSON):
    PROBE_PROCS=12 uv run python \
        .claude/skills/tpy-thir-wave/scripts/sites_multi.py

Writes /tmp/agents/thir-wave/sites_multi.json:
    {case: [["file.py:lineno", "reason"], ...]}
"""
from __future__ import annotations

import collections
import dataclasses
import json
import os
import shutil
import sys
import traceback
from pathlib import Path

ROOT = Path("/home/tommy/dev/turbo-python/tpy-m1")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import conftest as C  # noqa: E402
import tpyc.thir.fallback as F  # noqa: E402
from tpyc.compiler import Compiler  # noqa: E402
from tpyc.codegen_cpp import CodeGenOptions  # noqa: E402

OUT_DIR = Path("/tmp/agents/thir-wave")
_state = {"raises": set()}
_orig = F.ThirUnsupported.__init__


def _patched(self, r, *a, **kw):
    # Re-raise wrappers construct inside the inner raise's except block --
    # inherit the inner site so trampolines attribute to the blocking arm.
    inner = sys.exc_info()[1]
    inherited = getattr(inner, "_spy_site", None) \
        if isinstance(inner, F.ThirUnsupported) else None
    if inherited is not None:
        self._spy_site = inherited
    else:
        st = traceback.extract_stack()[:-1]
        frame = next((f for f in reversed(st)
                      if "/thir/lower/" in f.filename), None)
        if frame is not None:
            self._spy_site = f"{Path(frame.filename).name}:{frame.lineno}"
    site = getattr(self, "_spy_site", None)
    if site is not None:
        _state["raises"].add((site, r))
    _orig(self, r, *a, **kw)


F.ThirUnsupported.__init__ = _patched
OPTS = dataclasses.replace(
    CodeGenOptions(emit_source_comments=True, comment_line_numbers=False),
    thir_codegen=True)


def probe(case: str):
    cd = ROOT / "tests/cases" / case
    src = cd / "src/main.py"
    if not src.exists():
        # A frontend-plugin case (pascal/...) has no src/main.py. Returning
        # [] here would read as "raised nothing" -- i.e. a flip candidate.
        return [["<unprobed>", "no src/main.py"]]
    _state["raises"] = set()
    try:
        fr, extra = C._frontend_registry_for(src)
        comp = Compiler(src, default_int=C.get_case_default_int(cd),
                        lib_dirs=list(extra) + list(C.DEFAULT_LIB_DIRS),
                        frontend_registry=fr)
        mods = comp.compile()
        entry = next(m for m in mods if m.is_entry_point)
        sd = src.parent.resolve()
        for mod in mods:
            try:
                mod.path.resolve().relative_to(sd)
            except ValueError:
                continue
            comp.generate_code(mod, cd / "__tpyc_spy__",
                               entry_module_name=entry.name, options=OPTS)
    except Exception:
        pass
    finally:
        shutil.rmtree(cd / "__tpyc_spy__", ignore_errors=True)
    return sorted(_state["raises"])


def worker(case):
    try:
        return case, probe(case)
    except Exception:  # noqa: BLE001
        return case, []


def main():
    blockers = json.load(open(OUT_DIR / "blockers.json"))
    todo = sorted(n.replace("cases/", "", 1) for n in blockers)
    print(f"blocked cases: {len(todo)}", file=sys.stderr)

    from multiprocessing import Pool
    nproc = int(os.environ.get("PROBE_PROCS", "8"))
    per_case = {}
    with Pool(nproc) as pool:
        for i, (case, raises) in enumerate(
                pool.imap_unordered(worker, todo, chunksize=2)):
            per_case[case] = [list(x) for x in raises]
            if (i + 1) % 50 == 0:
                print(f"  {i + 1}/{len(todo)}", file=sys.stderr)

    json.dump(per_case, open(OUT_DIR / "sites_multi.json", "w"), indent=0)

    # Site histogram: how many cases touch each site at all.
    touch = collections.Counter()
    for raises in per_case.values():
        # Dedup by SITE: a case with several reasons at one site is ONE
        # touched case, not one per reason.
        for site in {x[0] for x in raises}:
            touch[site] += 1
    print(f"\n{len(per_case)} cases probed, "
          f"{sum(1 for v in per_case.values() if not v)} raised nothing\n")
    print("== sites by touched-case count ==")
    for site, n in touch.most_common(25):
        reasons = collections.Counter(
            r for v in per_case.values() for s, r in map(tuple, v)
            if s == site)
        rtop = ", ".join(f"{r}({k})" for r, k in reasons.most_common(2))
        print(f"{n:4d}  {site:<30} {rtop}")

    # Greedy set-cover: repeatedly take the site clearing which (assuming
    # no hidden chained blockers) contributes to the most flips.
    print("\n== greedy cover (site -> newly fully-cleared cases) ==")
    remaining = {c: {s for s, _r in map(tuple, v)}
                 for c, v in per_case.items() if v}
    cleared: set[str] = set()
    for _round in range(15):
        best, best_flips = None, -1
        for site in touch:
            if site in cleared:
                continue
            trial = cleared | {site}
            flips = sum(1 for ss in remaining.values() if ss <= trial)
            if flips > best_flips:
                best, best_flips = site, flips
        if best is None:
            break
        cleared.add(best)
        touched = sum(1 for ss in remaining.values() if best in ss)
        print(f"  +{best:<30} touches {touched:3d}  "
              f"cum flips {best_flips:3d}")


if __name__ == "__main__":
    main()
