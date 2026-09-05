"""Coverage guards over lib/tpy that outlive the second codegen path.

Every module under lib/tpy has its generated C++ committed by one ordinary
test case, tests/cases/harness/stdlib_render, whose options.json snapshots
`*`. That pattern matches only what the case COMPILES, so the case's import
list -- not the glob -- is what decides the coverage: a module nobody imports
there is matched by nothing, and the pattern reports no error because it did
match everything that was compiled. The same case also lands the whole library
in ONE expected/ tree, where two module names mapping to one snapshot path
would overwrite each other just as quietly.

The first two guards are name-level scans: no compile, no toolchain. The third
compiles the library and emits it, because what it asks -- does anything ever
DISPATCH to each arg-table family -- has no other reliable answer.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from conftest import TEST_CODEGEN_OPTIONS, module_to_expected_path

from tpyc import get_lib_dir
from tpyc.compiler import Compiler
# The two table modules are imported for their SIDE EFFECT: a family exists
# only once its `register_sink` call has run. conftest already pulls both in
# transitively, but the expected set below is derived from the registry, so
# an empty one would assert nothing -- naming them here keeps this guard from
# depending on somebody else's import graph.
from tpyc.thir.lower import arg_table, checks, expressions  # noqa: F401

LIB_TPY = get_lib_dir() / "tpy"

# The case that commits the library's render.
RENDER_CASE_MAIN = (Path(__file__).resolve().parent / "cases" / "harness"
                    / "stdlib_render" / "src" / "main.py")

# Self-check floor: the two scan guards below compare the case against the
# SCANNED module set, so a scan that quietly found nothing would assert
# nothing. Deliberately hard-coded well below the measured value (88 modules
# as of 2026-08-24) rather than tracking it, so it never needs touching.
MIN_MODULES = 80

# Detection sanity: macro modules run under CPython at compile time and emit no
# C++ at all, so they are excluded from the compile below. Over-exclusion is
# already caught by MIN_MODULES; this catches the mirror failure of a detector
# that suddenly classifies nothing (or everything) as a macro module.
MAX_MACRO_MODULES = 20

# The families the arg table has sinks for, snapshotted at COLLECTION time so
# a unit test registering a throwaway sink (tpyc/thir/test_arg_table.py has
# three) cannot join the set the reach floor demands. Derived from the
# registry rather than listed, so a family a later sink adds is covered
# without editing anything here.
THIR_ARG_FAMILIES = arg_table.registered_families()
THIR_ARG_CELLS = arg_table.registered_cells()

# Self-check floor for the family gate: a registry snapshotted before the sink
# modules were imported is empty, and an empty expected set is satisfied by
# reaching nothing. Well below the measured count (10 as of 2026-09-05) and
# deliberately loose in that direction: the count goes DOWN as two families
# that decide a shape the same way are united, which is not a regression.
MIN_ARG_FAMILIES = 8


def lib_module_names() -> list[str]:
    """Every importable module name under lib/tpy, package dirs included."""
    names: list[str] = []
    for path in sorted(LIB_TPY.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        parts = list(path.relative_to(LIB_TPY).parts)
        if parts[-1] == "__init__.py":
            parts = parts[:-1]
        else:
            parts[-1] = parts[-1][: -len(".py")]
        if parts:
            names.append(".".join(parts))
    return names


def is_macro_module(name: str) -> bool:
    path = LIB_TPY / (name.replace(".", "/") + ".py")
    if not path.exists():
        path = LIB_TPY / (name.replace(".", "/") + "/__init__.py")
    if not path.exists():
        return False
    # Directives live in the header comment block; the parser scans the same way.
    return "macro_module" in path.read_text(errors="replace")[:4000]


def compile_lib_tpy(tmp_path: Path) -> tuple[Compiler, list]:
    """Compile every non-macro lib/tpy module from ONE entry that imports them
    all: the dependency graph is walked once (~6s instead of ~73s) and each
    module is emitted with the union of the instantiations its siblings
    request, not just its own."""
    all_names = lib_module_names()
    macro_names = [n for n in all_names if is_macro_module(n)]
    names = [n for n in all_names if n not in set(macro_names)]
    assert 0 < len(macro_names) <= MAX_MACRO_MODULES, (
        f"macro_module detection returned {len(macro_names)} of "
        f"{len(all_names)} modules -- the detector is broken, not the stdlib"
    )
    entry = tmp_path / "thir_stdlib_gate_entry.py"
    entry.write_text("".join(f"import {n}\n" for n in names))
    compiler = Compiler(entry, lib_dirs=[LIB_TPY])
    compiled = compiler.compile()
    # A cycle peer also emits a `<mod>_fwd.hpp`, which generate_code_to_strings
    # has no slot for -- it would go unsnapshotted and undiffed in silence. No
    # lib/tpy module is one today; if that changes, this must grow a third
    # artifact rather than quietly stop covering it.
    assert not compiler._cycle_peers, (
        f"lib/tpy now has import cycles ({sorted(compiler._cycle_peers)}); "
        f"their _fwd.hpp headers are outside everything this gate compares")
    return compiler, compiled


def test_every_arg_family_is_reached(tmp_path: Path) -> None:
    """Every registered arg-table family is REACHED by the stdlib sweep.

    Not an audit join against the ladders the table replaced -- that one proved
    a cell decides what its ladder decided, and is gone with them. This proves
    anything ever asks, which has no other reliable answer: only a minority of
    the table's cells carry a face, so the zero-witness census covers some rows
    and no family as a whole.

    Asserted over ~2800 stdlib bodies through every callee shape the library
    uses, which no single corpus case reaches. Non-zero, never a fixed count:
    the population moves with every routing change. The family SET comes from
    the sink registry, so a step that adds a sink is covered without touching
    this -- and if the stdlib genuinely cannot reach a newly folded family,
    that is the gate saying the family has no witness, which is the thing
    worth knowing.
    """
    assert len(THIR_ARG_FAMILIES) >= MIN_ARG_FAMILIES, (
        f"only {len(THIR_ARG_FAMILIES)} arg-table families registered "
        f"(expected >= {MIN_ARG_FAMILIES}) -- the snapshot was taken before "
        f"the sink modules were imported, so this gate asserts nothing")

    compiler, compiled = compile_lib_tpy(tmp_path)
    for mod in compiled:
        if not mod.is_entry_point:
            compiler.generate_code_and_thir(mod, TEST_CODEGEN_OPTIONS)

    reached = arg_table.reached_families(compiler)
    missing = [f for f in THIR_ARG_FAMILIES if f not in reached]
    assert not missing, (
        f"the arg table's {', '.join(missing)} family/families decided ZERO "
        f"arguments over the whole stdlib sweep -- nothing dispatches to "
        f"them here, so their cells are unexercised and an over- or "
        f"under-admission in them has nothing pointed at it.\n"
        f"Reached: {sorted(reached)}\n"
        f"Check the gate that selects the sink still routes to it "
        f"(tpyc/thir/lower/checks.py, tpyc/thir/lower/expressions.py).")

    # Per-CELL coverage is REPORTED, never asserted. A cell no sweep reaches
    # is not a defect -- some are deliberate fences, some transcribe a ladder
    # leg the stdlib has no shape for -- so a floor here would be a
    # false-alarm generator. The number is a trend line, and having it at all
    # is the point: the fold's own coverage question went unanswerable once
    # its instruments came down.
    cells = arg_table.reached(compiler)
    decided = {(f, c.removeprefix("!")) for f, c in cells
               if c not in (arg_table.PROLOGUE_CELL, arg_table.NO_CELL)}
    print(f"\ntpy| thir arg-table: {len(THIR_ARG_FAMILIES)} families all "
          f"reached over {sum(cells.values())} argument verdicts; "
          f"{len(decided & THIR_ARG_CELLS)}/{len(THIR_ARG_CELLS)} cells "
          f"decided at least one")


def test_snapshot_path_scheme_round_trips() -> None:
    """The dotted-name -> path mapping is injective and reversible.

    A module name holds no `/` and a path component holds no `.`, so splitting
    on dots inverts exactly. The pair that looks like a collision is a package
    and its submodule: `tplib.json` lands on the file `json.hpp` beside the
    directory `json/` that `tplib.json.parser` lives in, which a filesystem
    holds side by side. Asserted over the whole library because the render case
    puts all of it in ONE expected/ tree, where a collision would silently
    overwrite one module's render with another's.
    """
    names = [n for n in lib_module_names() if not is_macro_module(n)]
    assert len(names) >= MIN_MODULES
    base = RENDER_CASE_MAIN.parent.parent / "expected"

    def invert(path: Path, ext: str) -> str:
        rel = path.relative_to(base)
        parts = list(rel.parts[1:])  # drop include/ or src/
        parts[-1] = parts[-1][: -len(ext)]
        return ".".join(parts)

    seen: dict[Path, str] = {}
    for name in names:
        for ext in (".hpp", ".cpp"):
            path = module_to_expected_path(base, name, ext)
            assert invert(path, ext) == name
            assert seen.setdefault(path, name) == name, (
                f"{name} and {seen[path]} both map to {path}")

    pkg = module_to_expected_path(base, "tplib.json", ".hpp")
    sub = module_to_expected_path(base, "tplib.json.parser", ".hpp")
    assert pkg != sub and sub.parent.name == "json"


def test_render_case_imports_every_lib_module() -> None:
    """The render case's import list equals the non-macro lib/tpy module set.

    Its options.json snapshots `*`, which takes every library module that
    COMPILES -- so the import list alone decides what gets a committed render.
    A module nobody imports there is matched by nothing, and the pattern
    reports no error because it did match the modules that were compiled.
    """
    want = {n for n in lib_module_names() if not is_macro_module(n)}
    tree = ast.parse(RENDER_CASE_MAIN.read_text())
    got = {a.name for node in tree.body if isinstance(node, ast.Import)
           for a in node.names}
    missing = sorted(want - got)
    extra = sorted(got - want)
    assert len(want) >= MIN_MODULES, (
        f"only {len(want)} non-macro modules found under {LIB_TPY} -- the "
        f"module scan broke, so this guard is comparing against nothing")
    if missing or extra:
        pytest.fail(
            f"{RENDER_CASE_MAIN} does not import every non-macro module under "
            f"{LIB_TPY}, so the library's committed render has holes.\n"
            + (f"  ADD these lines: "
               f"{', '.join('import ' + n for n in missing)}\n"
               if missing else "")
            + (f"  REMOVE these lines (no such non-macro module): "
               f"{', '.join('import ' + n for n in extra)}\n"
               if extra else "")
            + f"Then refresh with `uv run python tests/update_snapshots.py "
              f"-k stdlib_render`.",
            pytrace=False)
