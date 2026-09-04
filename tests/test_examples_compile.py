"""Every example under examples/ still reaches C++ code generation.

The corpus cases are written against the compiler; the examples are written
against the LANGUAGE, so they are the closest thing the suite has to code a
user would type. A construct that loses its only emitter turns them into
compile errors, and nothing else in the suite notices -- a snapshot case only
covers the shapes somebody already wrote a case for.

Front end only: no C++ toolchain, no build. There is no exception list: an
example that stops generating is a failure to fix in the compiler or in the
example, not a row to record.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import TEST_CODEGEN_OPTIONS

from tpyc import get_lib_dir
from tpyc.codegen_cpp.context import CodeGenError
from tpyc.compiler import Compiler

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

# Detection sanity: a glob that silently stopped matching would make this
# whole gate pass while compiling nothing. Well below the count today.
MIN_EXAMPLES = 5


def _example_paths() -> list[Path]:
    # Recursive: `examples/net/` holds the socket programs, and a glob that
    # stops at the top level would gate none of them.
    paths = sorted(p for p in EXAMPLES.rglob("*.py")
                   if "__tpyc__" not in p.parts)
    assert len(paths) >= MIN_EXAMPLES, (
        f"only {len(paths)} examples found under {EXAMPLES} -- the glob is "
        f"broken, not the examples directory")
    return paths


def _rel(path: Path) -> str:
    return path.relative_to(EXAMPLES).as_posix()


def _ids(paths: list[Path]) -> list[str]:
    return [_rel(p) for p in paths]


_PATHS = _example_paths()


def _emit(path: Path) -> None:
    compiler = Compiler(path, lib_dirs=[get_lib_dir() / "tpy"])
    modules = compiler.compile()
    entry = [m for m in modules if m.is_entry_point][0]
    compiler.generate_code_to_strings(entry, options=TEST_CODEGEN_OPTIONS)


@pytest.mark.parametrize("path", _PATHS, ids=_ids(_PATHS))
def test_example_generates_cpp(path: Path) -> None:
    name = _rel(path)
    try:
        _emit(path)
    except CodeGenError as err:
        pytest.fail(f"{name} no longer generates C++: {err}")
