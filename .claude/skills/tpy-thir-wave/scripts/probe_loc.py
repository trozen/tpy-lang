"""Reject-location spy: compile each named case with the THIR overlay and
print every ThirUnsupported raised during generation, with the nearest
statement/expression source line captured from the raising frame stack.

The fallback tally's tags are LOSSY (first-reject shadowing, coarse
families); this spy names the actual reason AND the source line, which is
what the grind loop needs to open the right case site + oracle hunk.

Usage (from the repo root):
    uv run python .claude/skills/tpy-thir-wave/scripts/probe_loc.py \
        group/case [group/case ...]
"""
from __future__ import annotations

import dataclasses
import inspect
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import conftest as C  # noqa: E402
from tpyc.compiler import Compiler  # noqa: E402
from tpyc.codegen_cpp import CodeGenOptions  # noqa: E402
from tpyc.thir import fallback as FB  # noqa: E402

SCRATCH = Path("/tmp/agents/thir-wave/__probe_out__")

_orig_init = FB.ThirUnsupported.__init__


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

    captured: list[tuple[str, object]] = []

    def spy(self, *args, **kwargs):
        _orig_init(self, *args, **kwargs)
        loc = None
        for frame_info in inspect.stack()[1:14]:
            frame_locals = frame_info.frame.f_locals
            for var in ("stmt", "e", "expr"):
                node = frame_locals.get(var)
                node_loc = getattr(node, "loc", None)
                if node_loc is not None and getattr(node_loc, "line", None):
                    loc = node_loc.line
                    break
            if loc:
                break
        captured.append((args[0] if args else "?", loc))

    FB.ThirUnsupported.__init__ = spy
    try:
        for mod in compiled_modules:
            try:
                mod.path.resolve().relative_to(src_dir)
            except ValueError:
                continue
            compiler.generate_code(mod, out,
                                   entry_module_name=entry_module.name,
                                   options=thir_opts)
    finally:
        FB.ThirUnsupported.__init__ = _orig_init
    print(f"=== {case} ===")
    print(f"  final fallback: {dict(compiler._thir_fallback)}")
    for (reason, line), n in Counter(captured).most_common(25):
        print(f"  {n}x line {line}: {reason}")


if __name__ == "__main__":
    for case_name in sys.argv[1:]:
        probe(case_name)
