"""The committed tuple-element-vs-singleton matrix, asserted cell by cell.

The generator, the runner and the table live in
`scripts/thir_migration/review/tuple_element_matrix.py` -- see its docstring
for what a cell records. This module is the gate over the VERDICT and
WARNING columns only: it fails when a tuple program's accept / reject / warn
outcome moves, so such a change is a diff in the committed table rather than
something the next census has to find. The emitted-form and CPython-parity
columns are deliberately NOT gated: any render change moves the form, and the
parity needs a build, so gating them would put a local C++ build on every
branch that touches emission. They are refreshed by whoever works a tuple
unit (`docs/TUPLE_COMPLETION_PLAN.md`), and the script's own check diffs them.

It lives beside the case suite rather than under `tpyc/` for the reason
`test_property_position_sweep.py` gives: it is front-end work per cell, not a
unit test, and the sdist ships `tpyc/` without `scripts/` -- hence the lazy
load and the skip.

One test per cell, so the failure names the cell and the run distributes over
xdist workers.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REVIEW = (Path(__file__).resolve().parents[1]
           / "scripts" / "thir_migration" / "review")
_SCRIPT = _REVIEW / "tuple_element_matrix.py"

pytestmark = pytest.mark.skipif(
    not _SCRIPT.exists(),
    reason="scripts/thir_migration/review is not part of this checkout")

_matrix = None


def _load():
    """Import the script on first use, never at collection."""
    global _matrix
    if _matrix is None:
        spec = importlib.util.spec_from_file_location(
            "tuple_element_matrix", _SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("tuple_element_matrix", mod)
        spec.loader.exec_module(mod)
        _matrix = mod
    return _matrix


def _expected() -> dict[str, list[str]]:
    if not _SCRIPT.exists():
        return {}
    return json.loads((_REVIEW / "tuple_element_matrix.expected.json")
                      .read_text())


@pytest.fixture(scope="session")
def matrix_programs(tmp_path_factory) -> dict[str, Path]:
    out = tmp_path_factory.mktemp("tuple_matrix")
    return _load().generate(out)


@pytest.mark.parametrize("cell", sorted(_expected()))
def test_cell_unchanged(cell: str, matrix_programs) -> None:
    src = matrix_programs.get(cell)
    assert src is not None, (
        f"{cell} is in the committed table but the generator no longer "
        f"produces it -- rerun the script with --update and say why in the "
        f"commit")
    build = src.parent / "build" / cell
    build.mkdir(parents=True, exist_ok=True)
    assert _load().cell_verdict(src, build, all_warnings=True) == \
        _expected()[cell][:2], (
        f"the verdict or a warning at {cell} moved; if that is intended, "
        f"rerun "
        f"`python {_SCRIPT.relative_to(Path(__file__).resolve().parents[1])} "
        f"--update` and state the move in the commit body")


def test_generator_and_table_agree(matrix_programs) -> None:
    """Every generated cell has a committed row, and vice versa."""
    assert sorted(matrix_programs) == sorted(_expected())
