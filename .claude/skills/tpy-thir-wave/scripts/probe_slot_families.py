"""Slot-TYPE-family census for a `note_detail` tag whose units all share one
detail string (`decl.slot_type`, `return.slot_type`, ...).

`probe_site_units.py` ranks sites; a site whose whole mass carries ONE detail
is still opaque -- this reads the rejecting frame's local variable (the slot
type) and tallies its family, which is what actually picks the rows.

Usage (from the repo root):
    PROBE_PROCS=10 uv run python \
        .claude/skills/tpy-thir-wave/scripts/probe_slot_families.py \
        decl.slot_type vtype
"""
from __future__ import annotations

import collections
import dataclasses
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import conftest as C  # noqa: E402
import tpyc.thir.fallback as FB  # noqa: E402
from tpyc.compiler import Compiler  # noqa: E402
from tpyc.codegen_cpp import CodeGenOptions  # noqa: E402

TAG = sys.argv[1] if len(sys.argv) > 1 else "decl.slot_type"
VAR = sys.argv[2] if len(sys.argv) > 2 else "vtype"

_hits: collections.Counter = collections.Counter()
_orig_detail = FB.note_detail


def _detail(reason: str):
    if reason == TAG:
        frame = sys._getframe(1)
        t = frame.f_locals.get(VAR)
        _hits[_family(t)] += 1
    return _orig_detail(reason)


def _family(t) -> str:
    if t is None:
        return "None"
    name = type(t).__name__
    try:
        from tpyc.typesys import OptionalType, UnionType, TupleType, OwnType
        from tpyc.thir.lower.predicates import is_list, is_dict, is_set
        if isinstance(t, OptionalType):
            return f"Optional[{type(t.inner).__name__}]" + (
                " ptr" if t.uses_pointer_repr() else " value")
        if isinstance(t, UnionType):
            return "Union" + (" ptr" if t.uses_pointer_repr() else " value")
        if isinstance(t, TupleType):
            return "Tuple"
        if isinstance(t, OwnType):
            return f"Own[{type(t.wrapped).__name__}]"
        if is_list(t):
            return "list"
        if is_dict(t):
            return "dict"
        if is_set(t):
            return "set"
    except Exception:  # noqa: BLE001
        pass
    return f"{name}:{t}"[:60]


FB.note_detail = _detail
for mod in ("tpyc.thir.lower.statements", "tpyc.thir.lower.expressions",
            "tpyc.thir.lower.checks", "tpyc.thir.lower.functions"):
    __import__(mod)
    m = sys.modules[mod]
    if hasattr(m, "note_detail"):
        m.note_detail = _detail


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
    out = case_dir / "__tpyc_fam__"
    for mod in mods:
        try:
            mod.path.resolve().relative_to(sd)
        except ValueError:
            continue
        comp.generate_code(mod, out, entry_module_name=entry.name,
                           options=opts)
    shutil.rmtree(out, ignore_errors=True)


def worker(args):
    _hits.clear()
    name, cd, ms = args
    try:
        probe(Path(cd), Path(ms))
    except Exception:  # noqa: BLE001
        pass
    shutil.rmtree(Path(cd) / "__tpyc_fam__", ignore_errors=True)
    return list(_hits.items())


def main():
    cases = C.discover_cases()
    marked = [(n, str(cd), str(ms)) for (n, cd, ms) in cases
              if (cd / "no_thir.txt").exists()]
    from multiprocessing import Pool
    agg: collections.Counter = collections.Counter()
    with Pool(int(os.environ.get("PROBE_PROCS", "8"))) as pool:
        for rows in pool.imap_unordered(worker, marked, chunksize=4):
            for k, n in rows:
                agg[k] += n
    print(f"\n{TAG}: {sum(agg.values())} raises over {len(marked)} cases\n")
    for k, n in agg.most_common(30):
        print(f"{n:5d}  {k}")


if __name__ == "__main__":
    main()
