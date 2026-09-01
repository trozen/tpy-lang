"""The library's committed render cannot silently narrow.

Every module under lib/tpy has its generated C++ committed by one ordinary
test case, tests/cases/harness/stdlib_render, whose options.json snapshots
`*`. That pattern matches only what the case COMPILES, so the case's import
list -- not the glob -- is what decides the coverage: a module nobody imports
there is matched by nothing, and the pattern reports no error because it did
match everything that was compiled. The same case also lands the whole library
in ONE expected/ tree, where two module names mapping to one snapshot path
would overwrite each other just as quietly.

Both guards are name-level scans over lib/tpy: no compile, no toolchain.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from conftest import module_to_expected_path

from tpyc import get_lib_dir

LIB_TPY = get_lib_dir() / "tpy"

# The case that commits the library's render.
RENDER_CASE_MAIN = (Path(__file__).resolve().parent / "cases" / "harness"
                    / "stdlib_render" / "src" / "main.py")

# Self-check floor: both guards below compare the case against the SCANNED
# module set, so a scan that quietly found nothing would assert nothing.
# Deliberately hard-coded well below the measured value (88 modules as of
# 2026-08-24) rather than tracking it, so it never needs touching.
MIN_MODULES = 80


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
