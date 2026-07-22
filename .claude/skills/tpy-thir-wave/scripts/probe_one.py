"""Single-case THIR probe: compile each named case with the THIR overlay,
print its per-body fallback reasons, and byte-diff the THIR-generated user
modules against the committed expected/ snapshots.

Usage (from the repo root):
    uv run python .claude/skills/tpy-thir-wave/scripts/probe_one.py \
        group/case [group/case ...]

Output per case: the distinct fallback reasons with body counts (or CLEAN),
plus IDENTICAL/DIVERGES for the snapshot byte-diff. A case is a flip
candidate only when it is BOTH clean and identical.
"""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import conftest as C  # noqa: E402
from tpyc.compiler import Compiler  # noqa: E402
from tpyc.codegen_cpp import CodeGenOptions  # noqa: E402

SCRATCH = Path("/tmp/agents/thir-wave/__probe_out__")


def probe(case: str) -> None:
    case_dir = ROOT / "tests" / "cases" / case
    main_src = case_dir / "src" / "main.py"
    frontend_registry, extra_lib_dirs = C._frontend_registry_for(main_src)
    default_int = C.get_case_default_int(case_dir)
    lib_dirs = list(extra_lib_dirs) + list(C.DEFAULT_LIB_DIRS)
    thir_opts = dataclasses.replace(
        CodeGenOptions(emit_source_comments=True, comment_line_numbers=False),
        thir_codegen=True)
    compiler = Compiler(main_src, default_int=default_int, lib_dirs=lib_dirs,
                        frontend_registry=frontend_registry)
    compiled_modules = compiler.compile()
    entry_module = next(m for m in compiled_modules if m.is_entry_point)
    src_dir = main_src.parent.resolve()
    out = SCRATCH / case.replace("/", "_")
    for mod in compiled_modules:
        try:
            mod.path.resolve().relative_to(src_dir)
        except ValueError:
            continue
        compiler.generate_code(mod, out, entry_module_name=entry_module.name,
                               options=thir_opts)
    print(f"=== {case} ===")
    for reason, count in sorted(compiler._thir_fallback.items()):
        print(f"  {reason}: {count}")
    if not compiler._thir_fallback:
        print("  CLEAN (no fallback)")
    # Byte-diff every generated user-module file against expected/.
    expected = case_dir / "expected"
    diverged = []
    for gen in sorted(out.rglob("*.[ch]pp")):
        rel = gen.relative_to(out)
        # Generated tree nests under <module>.d/{include,src}/; expected/
        # keeps {include,src}/ at the case root.
        parts = [p for p in rel.parts if not p.endswith(".d")]
        exp = expected.joinpath(*parts)
        if not exp.exists():
            diverged.append(f"{rel} (no expected counterpart)")
        elif gen.read_bytes() != exp.read_bytes():
            diverged.append(str(rel))
    if diverged:
        for d in diverged:
            print(f"  DIVERGES: {d}")
    else:
        print("  IDENTICAL")


if __name__ == "__main__":
    for case_name in sys.argv[1:]:
        probe(case_name)
