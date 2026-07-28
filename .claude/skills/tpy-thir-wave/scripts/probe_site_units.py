"""Per-RAISE-SITE fallback UNIT census over the whole marked corpus.

`probe_sites.py` ranks a tag's sole-blocker CASES; this ranks its fallback
UNITS (bodies), which is the linear metric. A site holding few cases can still
hold many units (one case with twenty bodies) and vice versa -- pick the target
with the metric you are steering by.

Attribution mirrors the fallback module's own coarseness contract: the FIRST
`ThirUnsupported` raised during an attempt names the site, exactly as
`note()`'s set-if-empty names the reason.

Usage (from the repo root):
    PROBE_PROCS=10 uv run python \
        .claude/skills/tpy-thir-wave/scripts/probe_site_units.py [reason-prefix]
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

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import conftest as C  # noqa: E402
import tpyc.thir.fallback as FB  # noqa: E402
from tpyc.compiler import Compiler  # noqa: E402
from tpyc.codegen_cpp import CodeGenOptions  # noqa: E402

PREFIX = sys.argv[1] if len(sys.argv) > 1 else ""
OUT = Path("/tmp/agents/thir-wave/site_units.json")

_state = {"first": None}
_orig_init = FB.ThirUnsupported.__init__
_orig_begin = FB.begin_attempt
_orig_fold = FB.fold_attempt
_tally: collections.Counter = collections.Counter()


def _init(self, reason, *a, **kw):
    if _state["first"] is None:
        st = traceback.extract_stack()[:-1]
        frame = next((f for f in reversed(st)
                      if "/thir/lower/" in f.filename), None)
        if frame is not None:
            _state["first"] = f"{Path(frame.filename).name}:{frame.lineno}"
    _orig_init(self, reason, *a, **kw)


def _begin():
    _state["first"] = None
    _orig_begin()


def _fold(component, node=None):
    from tpyc.compilation_context import get_current_compiler
    comp = get_current_compiler()
    reason = (comp._thir_reject_reason if comp is not None else None) or "unclassified"
    detail = (comp._thir_reject_detail if comp is not None else None) or "-"
    key = f"{component}:{reason}"
    if key.startswith(PREFIX):
        _tally[(key, f"{_state['first'] or '?'}  [{detail}]")] += 1
    _orig_fold(component, node)


FB.ThirUnsupported.__init__ = _init
FB.begin_attempt = _begin
FB.fold_attempt = _fold


def probe(case_dir: Path, main_src: Path):
    fr, extra = C._frontend_registry_for(main_src)
    opts = dataclasses.replace(
        CodeGenOptions(emit_source_comments=True, comment_line_numbers=False),
        thir_codegen=True)
    comp = Compiler(main_src, default_int=C.get_case_default_int(case_dir),
                    lib_dirs=list(extra) + list(C.DEFAULT_LIB_DIRS),
                    frontend_registry=fr)
    mods = comp.compile()
    entry = next(m for m in mods if m.is_entry_point)
    sd = main_src.parent.resolve()
    out = case_dir / "__tpyc_units__"
    for mod in mods:
        try:
            mod.path.resolve().relative_to(sd)
        except ValueError:
            continue
        comp.generate_code(mod, out, entry_module_name=entry.name,
                           options=opts)
    shutil.rmtree(out, ignore_errors=True)


def worker(args):
    _tally.clear()
    name, cd, ms = args
    try:
        probe(Path(cd), Path(ms))
    except Exception:  # noqa: BLE001
        pass
    shutil.rmtree(Path(cd) / "__tpyc_units__", ignore_errors=True)
    return [(k, s, n) for (k, s), n in _tally.items()]


def main():
    cases = C.discover_cases()
    marked = [(n, str(cd), str(ms)) for (n, cd, ms) in cases
              if (cd / "no_thir.txt").exists()]
    print(f"marked cases: {len(marked)}", file=sys.stderr)
    from multiprocessing import Pool
    agg: collections.Counter = collections.Counter()
    with Pool(int(os.environ.get("PROBE_PROCS", "8"))) as pool:
        for i, rows in enumerate(pool.imap_unordered(worker, marked,
                                                     chunksize=4)):
            for k, s, n in rows:
                agg[(k, s)] += n
            if (i + 1) % 200 == 0:
                print(f"  {i + 1}/{len(marked)}", file=sys.stderr)

    per_site: collections.Counter = collections.Counter()
    for (k, s), n in agg.items():
        per_site[(k, s)] = n
    print(f"\nTOTAL units matching '{PREFIX}': {sum(per_site.values())}\n")
    for (k, s), n in per_site.most_common(30):
        print(f"{n:5d}  {k:<42} {s}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({f"{k}|{s}": n for (k, s), n in agg.items()},
                              indent=0))


if __name__ == "__main__":
    main()
