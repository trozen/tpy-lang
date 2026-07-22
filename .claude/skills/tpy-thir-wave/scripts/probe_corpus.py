"""Full-corpus per-case THIR blocker map: for every no_thir-marked case,
compile with the THIR overlay and record the distinct fallback reasons.

The output JSON keys the SOLE-BLOCKER analysis that drives cell selection:
a case whose `reasons` list has exactly one entry is one arm away from
flipping. CPU-heavy (~5-10 min at PROBE_PROCS=8..10); do not run while
another suite is running.

Usage (from the repo root):
    PROBE_PROCS=10 uv run python \
        .claude/skills/tpy-thir-wave/scripts/probe_corpus.py

Writes /tmp/agents/thir-wave/blockers.json:
    {case: {"reasons": [...], "counts": {...}} | {"error": ...}}
Rank sole blockers with:
    uv run python -c "import json,collections; d=json.load(open('/tmp/agents/thir-wave/blockers.json')); c=collections.Counter(r['reasons'][0] for r in d.values() if len(r.get('reasons',[]))==1); [print(f'{n:4d}  {k}') for k,n in c.most_common(40)]"
"""
from __future__ import annotations

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
from tpyc.compiler import Compiler  # noqa: E402
from tpyc.codegen_cpp import CodeGenOptions  # noqa: E402

OUT_PATH = Path("/tmp/agents/thir-wave/blockers.json")


def probe_case(case_dir: Path, main_src: Path) -> dict:
    frontend_registry, extra_lib_dirs = C._frontend_registry_for(main_src)
    default_int = C.get_case_default_int(case_dir)
    lib_dirs = list(extra_lib_dirs) + list(C.DEFAULT_LIB_DIRS)
    thir_opts = dataclasses.replace(
        CodeGenOptions(emit_source_comments=True, comment_line_numbers=False),
        thir_codegen=True)

    compiler = Compiler(main_src, default_int=default_int, lib_dirs=lib_dirs,
                        frontend_registry=frontend_registry)
    compiled_modules = compiler.compile()
    from tpyc.sema import DiagnosticLevel
    for mod in compiled_modules:
        if mod.analyzer:
            for d in mod.analyzer.diagnostics:
                if d.level == DiagnosticLevel.ERROR:
                    return {"error": "compile_error"}

    entry_module = next(m for m in compiled_modules if m.is_entry_point)
    src_dir = main_src.parent.resolve()
    out = case_dir / "__tpyc_probe__"
    for mod in compiled_modules:
        try:
            mod.path.resolve().relative_to(src_dir)
        except ValueError:
            continue
        compiler.generate_code(mod, out, entry_module_name=entry_module.name,
                               options=thir_opts)
    return {"reasons": sorted(compiler._thir_fallback.keys()),
            "counts": dict(compiler._thir_fallback)}


def worker(args):
    name, case_dir_s, main_src_s = args
    try:
        res = probe_case(Path(case_dir_s), Path(main_src_s))
        res["name"] = name
        return res
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "error": f"{type(exc).__name__}: {exc}",
                "tb": traceback.format_exc()[-500:]}


def main():
    cases = C.discover_cases()
    no_thir = [(n, str(cd), str(ms)) for (n, cd, ms) in cases
               if (cd / "no_thir.txt").exists()]
    print(f"no_thir cases: {len(no_thir)}", file=sys.stderr)

    from multiprocessing import Pool
    nproc = int(os.environ.get("PROBE_PROCS", "8"))
    results = {}
    with Pool(nproc) as pool:
        for i, r in enumerate(pool.imap_unordered(worker, no_thir,
                                                  chunksize=4)):
            results[r["name"]] = r
            if (i + 1) % 100 == 0:
                print(f"  {i + 1}/{len(no_thir)}", file=sys.stderr)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(results, indent=0))
    print(f"wrote {OUT_PATH}", file=sys.stderr)

    for (_, cd, _) in no_thir:
        p = Path(cd) / "__tpyc_probe__"
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)


if __name__ == "__main__":
    main()
