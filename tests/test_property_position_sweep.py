"""The committed property x position verdict table, asserted cell by cell.

The generator, the runner and the table live in
`scripts/thir_migration/review/property_position_sweep.py` -- see its
docstring for what a cell records (a compile verdict and the first warning,
nothing about behaviour) and for why this is an instrument rather than an arm
pin. This module is the gate over it: it runs the COMP half and fails on any
cell whose verdict or warning moved, so a change to what a `@property` read
is allowed to do at some position is a diff in the committed table rather
than something the next review has to find.

It lives beside the case suite rather than under `tpyc/`, because it compiles
a generated corpus rather than pinning an arm, and that does not belong in the
toolchain-free unit tier that exists to be fast. The sdist also ships `tpyc/`
without `scripts/`, so the script it drives is not always there -- which is
why it is loaded lazily and the module skips when it is absent. The generated
corpus goes in a pytest tmp directory so the run leaves nothing behind.

One test per PROGRAM, not per cell. A position's whole cell set shares one
program and one compilation now that `collect_thir(tolerate_reject=True)`
surveys every body instead of stopping at the first reject; only the cells
that lower DURING emission -- a generator, an `async def`, the module-init
body -- still need a program each, because a reject there ends the pass.
"""
from __future__ import annotations

import importlib.util
import json
import re
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


def _by_program() -> dict[str, dict[str, list[str]]]:
    sweep = _load()
    out: dict[str, dict[str, list[str]]] = {}
    for cell, value in _expected().items():
        out.setdefault(sweep.program_of(cell), {})[cell] = value
    return out


_PROGRAMS = sorted(_by_program()) if _SCRIPT.exists() else []

# A verdict that is not a dotted reject tag is a sema message, and sema stops
# the module at its first error -- so verifying those inside the position's
# program recompiles the whole program once per cell. They are checked one
# MINIMAL program each instead, in chunks, to bound what any item carries.
_TAG = re.compile(r"^[a-z_]+(\.[a-z_0-9]+)*(:[a-z_0-9.]+)*$")
_SEMA_CHUNK = 4


def _is_sema(verdict: str) -> bool:
    return verdict != "ok" and not _TAG.match(verdict)


def _sema_chunks() -> list[tuple[str, tuple[str, ...]]]:
    out = []
    for program, want in sorted(_by_program().items()):
        sema = sorted(c for c in want if _is_sema(want[c][0]))
        for i in range(0, len(sema), _SEMA_CHUNK):
            out.append((program, tuple(sema[i:i + _SEMA_CHUNK])))
    return out


_SEMA = _sema_chunks() if _SCRIPT.exists() else []


@pytest.fixture(scope="session")
def sweep_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("prop_sweep")


def _fail(want, got, cells) -> None:
    moved = [f"{c}: {want[c]} -> {got.get(c)}"
             for c in sorted(cells) if got.get(c) != want[c]]
    assert not moved, (
        "the verdict or warning moved for:\n  " + "\n  ".join(moved)
        + f"\nif that is intended, rerun `python "
        f"{_SCRIPT.relative_to(Path(__file__).resolve().parents[1])} "
        f"--update` and state the move in the commit body")


@pytest.mark.parametrize("program", _PROGRAMS)
def test_program_verdicts_unchanged(program: str, sweep_dir: Path) -> None:
    want = _by_program()[program]
    sema = {c for c in want if _is_sema(want[c][0])}
    got = _load().survey(program, sweep_dir, expect_sema=sema)
    _fail(want, got, set(want) - sema)


@pytest.mark.parametrize("program,cells", _SEMA,
                         ids=[f"{p}-{i}" for i, (p, _c) in enumerate(_SEMA)])
def test_sema_cells_unchanged(program: str, cells: tuple[str, ...],
                              sweep_dir: Path) -> None:
    got = _load().survey_sema(program, sweep_dir, list(cells))
    _fail(_by_program()[program], got, cells)


def test_generator_and_table_agree() -> None:
    """Every generated cell has a committed verdict, and vice versa."""
    sweep = _load()
    generated = {c[2] for c in sweep._cells_index()}
    assert sorted(generated) == sorted(_expected())
