"""Dual-path divergence smoke: generate a SCRATCH program through both the
AST path and the THIR overlay and unified-diff the outputs.

This is the honest check for ADMITTED-BUT-UNWITNESSED shapes: the corpus
byte-diff only covers shapes some committed case reaches, so any newly
widened arm should be smoked here with adversarial inputs (slot-threaded
vs target-less positions, Own/Optional wrappers, negations) BEFORE trusting
the corpus green. A non-empty `fallback` line means the shape fell back --
byte-identity via fallback proves routing did NOT happen, not that the arm
is correct.

ONE WIDTH IS NOT ENOUGH. `default_int` is compilation-wide and reaches every
module's sema, so a divergence gated on it is invisible at any single setting --
and at least one known divergence appears at BigInt and at neither of the others.
Smoke a shape at all three unless there is a reason not to.

The scratch tree is keyed by file STEM, so probes that share a name (a tree of
`main.py`) overwrite each other and the later one reads as agreement. Give
probes distinct stems, or point this at one file at a time.

Needs BOTH emit paths, so it does not survive the AST body-emitter deletion as
written -- like the move-verdict and binding joins, its replacement is part of
the cutover decision, not a follow-on.

Usage (from the repo root):
    uv run python .claude/skills/tpy-thir-wave/scripts/dualgen.py \
        /tmp/agents/thir-wave/smoke/main.py [Int32] [Int64] [BigInt]
"""
from __future__ import annotations

import dataclasses
import difflib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import conftest as C  # noqa: E402
from tpyc.compiler import Compiler  # noqa: E402
from tpyc.codegen_cpp import CodeGenOptions  # noqa: E402

SCRATCH = Path("/tmp/agents/thir-wave/__dual__")


def gen(main_src: Path, out: Path, thir: bool, default_int: str = "Int32"):
    lib_dirs = list(C.DEFAULT_LIB_DIRS)
    opts = dataclasses.replace(
        CodeGenOptions(emit_source_comments=True, comment_line_numbers=False),
        thir_codegen=thir)
    compiler = Compiler(main_src, default_int=default_int, lib_dirs=lib_dirs)
    compiled_modules = compiler.compile()
    entry_module = next(m for m in compiled_modules if m.is_entry_point)
    src_dir = main_src.parent.resolve()
    for mod in compiled_modules:
        try:
            mod.path.resolve().relative_to(src_dir)
        except ValueError:
            continue
        compiler.generate_code(mod, out, entry_module_name=entry_module.name,
                               options=opts)
    return dict(compiler._thir_fallback)


def smoke(src: Path, default_int: str) -> None:
    # The scratch tree is keyed by width as well as by stem, so the three runs
    # cannot overwrite each other's output and read as agreement.
    base = SCRATCH / src.stem / default_int
    fallback = gen(src, base / "thir", thir=True, default_int=default_int)
    gen(src, base / "ast", thir=False, default_int=default_int)
    print(f"[{default_int}] fallback: {fallback}")
    ok = True
    cmp = 0
    for f in sorted((base / "ast").rglob("*.[ch]pp")):
        rel = f.relative_to(base / "ast")
        tf = base / "thir" / rel
        a = f.read_text().splitlines()
        b = tf.read_text().splitlines()
        cmp += 1
        if a != b:
            ok = False
            print(f"[{default_int}] DIVERGES: {rel}")
            for line in difflib.unified_diff(a, b, "ast", "thir",
                                             lineterm="", n=1):
                print(line)
    # `cmp` guards the vacuous green: a path-mapping slip that finds no
    # counterpart file skips silently and would otherwise print IDENTICAL
    # having compared nothing.
    print(f"[{default_int}] cmp={cmp}")
    if cmp == 0:
        print(f"[{default_int}] VACUOUS -- compared zero files")
        return
    print(f"[{default_int}] " + ("IDENTICAL" if ok else "DIVERGENT"))


def main():
    src = Path(sys.argv[1]).resolve()
    for width in (sys.argv[2:] or ["Int32"]):
        smoke(src, width)


if __name__ == "__main__":
    main()
