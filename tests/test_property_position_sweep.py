"""The committed property x position verdict table, asserted cell by cell.

The generator, the runner and the table live in
`scripts/thir_migration/review/property_position_sweep.py` -- see its
docstring for what a cell records (a compile verdict and the first warning,
nothing about behaviour) and for why this is an instrument rather than an arm
pin. This module is the gate over it: it runs the COMP half and fails on any
cell whose verdict or warning moved, so a change to what a `@property` read
is allowed to do at some position is a diff in the committed table rather
than something the next review has to find.

It lives beside the case suite rather than under `tpyc/`. The cost is the
reason: 772 cells at roughly half a second of front-end work each is about six
CPU-minutes, which does not belong in the toolchain-free unit tier that exists
to be fast (moving it out took that tier from 2757 tests in ~83 s to 2260 in
~21 s). The sdist also ships `tpyc/` without `scripts/`, so the script it
drives is not always there -- which is why it is loaded lazily and the module
skips when it is absent. The generated corpus goes in a pytest tmp directory
so the run does not leave 772 programs behind.

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
_SCRIPT = _REVIEW / "property_position_sweep.py"

pytestmark = pytest.mark.skipif(
    not _SCRIPT.exists(),
    reason="scripts/thir_migration/review is not part of this checkout")

_sweep = None


def _load():
    """Import the script on first use, never at collection."""
    global _sweep
    if _sweep is None:
        spec = importlib.util.spec_from_file_location(
            "property_position_sweep", _SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("property_position_sweep", mod)
        spec.loader.exec_module(mod)
        _sweep = mod
    return _sweep


def _expected() -> dict[str, list[str]]:
    if not _SCRIPT.exists():
        return {}
    return json.loads((_REVIEW / "property_position_sweep.expected.json")
                      .read_text())


@pytest.fixture(scope="session")
def sweep_programs(tmp_path_factory) -> dict[str, Path]:
    out = tmp_path_factory.mktemp("prop_sweep")
    return _load().generate(out)


@pytest.mark.parametrize("cell", sorted(_expected()))
def test_cell_verdict_unchanged(cell: str, sweep_programs) -> None:
    src = sweep_programs.get(cell)
    assert src is not None, (
        f"{cell} is in the committed table but the generator no longer "
        f"produces it -- rerun the script with --update and say why in the "
        f"commit")
    build = src.parent / "build" / cell
    build.mkdir(parents=True, exist_ok=True)
    assert _load()._verdict(src, build) == _expected()[cell], (
        f"the verdict or warning at {cell} moved; if that is intended, rerun "
        f"`python {_SCRIPT.relative_to(Path(__file__).resolve().parents[1])} "
        f"--update` and state the move in the commit body")


def test_generator_and_table_agree(sweep_programs) -> None:
    """Every generated cell has a committed verdict, and vice versa."""
    assert sorted(sweep_programs) == sorted(_expected())
