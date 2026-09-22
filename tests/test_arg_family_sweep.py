"""The committed argument-source x slot x callee-family verdict table.

The generator, the survey and the table live in
`scripts/thir_migration/review/arg_family_sweep.py` -- see its docstring for
what a cell records (a compile verdict, the first warning and the lowered
subject statement, nothing about behaviour) and for why this is an instrument
rather than an arm pin. This module is the gate over it: it surveys every cell
and fails on any whose verdict, warning or render moved, so a change to what
one callee family admits where its siblings do not is a diff in the committed
table rather than something the next review has to find.

One test per PROGRAM, not per cell. A program holds one parameter slot's whole
column set -- hundreds of cells, each its own caller function -- and the THIR
survey answers all of them from one compilation, so the gate costs about one
compilation per program plus one per sema cell instead of one process per
cell. The committed cell set is handed to the survey so a row the table
already records as type-invalid is not generated and costs nothing.

It lives beside the case suite rather than under `tpyc/` because it compiles a
generated corpus rather than pinning an arm, which is where the property
sweep lives for the same reason. The sdist ships `tpyc/` without `scripts/`,
so the script it drives is not always there -- hence the lazy load and the
skip when it is absent.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

import pytest

_REVIEW = (Path(__file__).resolve().parents[1]
           / "scripts" / "thir_migration" / "review")
_SCRIPT = _REVIEW / "arg_family_sweep.py"

pytestmark = pytest.mark.skipif(
    not _SCRIPT.exists(),
    reason="scripts/thir_migration/review is not part of this checkout")

_sweep = None


def _load():
    """Import the script on first use, never at collection."""
    global _sweep
    if _sweep is None:
        spec = importlib.util.spec_from_file_location("arg_family_sweep",
                                                      _SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("arg_family_sweep", mod)
        spec.loader.exec_module(mod)
        _sweep = mod
    return _sweep


def _expected() -> dict[str, list[str]]:
    if not _SCRIPT.exists():
        return {}
    return json.loads((_REVIEW / "arg_family_sweep.expected.json").read_text())


def _by_program() -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = defaultdict(dict)
    sweep = _load()
    for cell, value in _expected().items():
        out[sweep.program_of(cell)][cell] = value
    return dict(out)


_PROGRAMS = sorted(_by_program()) if _SCRIPT.exists() else []

# Verifying a sema cell means compiling a program that refuses, and sema stops
# at its first error -- so a slot with twenty of them would recompile its whole
# program twenty times inside one item. They are checked one MINIMAL program
# each instead, in chunks, so no item carries more than a few seconds of work.
_SEMA_CHUNK = 4


def _sema_chunks() -> list[tuple[str, tuple[str, ...]]]:
    out = []
    for program, want in sorted(_by_program().items()):
        sema = sorted(c for c in want if want[c][0].startswith("sema:"))
        for i in range(0, len(sema), _SEMA_CHUNK):
            out.append((program, tuple(sema[i:i + _SEMA_CHUNK])))
    return out


_SEMA = _sema_chunks() if _SCRIPT.exists() else []


@pytest.fixture(scope="session")
def sweep_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("arg_sweep")


def _fail(want, got, cells):
    moved = [f"{c}: {want[c]} -> {got.get(c)}"
             for c in sorted(cells) if got.get(c) != want[c]]
    assert not moved, (
        "the verdict, warning or render moved for:\n  " + "\n  ".join(moved)
        + f"\nif that is intended, rerun `python "
        f"{_SCRIPT.relative_to(Path(__file__).resolve().parents[1])} "
        f"--update` and state the move in the commit body")


@pytest.mark.parametrize("program", _PROGRAMS)
def test_program_verdicts_unchanged(program: str, sweep_dir: Path) -> None:
    sweep = _load()
    want = _by_program()[program]
    cells = sorted({tuple(c.rsplit("__", 2)[-2:]) for c in want})
    sema = sorted({tuple(c.rsplit("__", 2)[-2:]) for c in want
                   if want[c][0].startswith("sema:")})
    got = sweep.survey(program, sweep_dir, cells=cells, expect_sema=sema)
    _fail(want, got, set(want) - {c for c in want
                                 if want[c][0].startswith("sema:")})


@pytest.mark.parametrize("program,cells", _SEMA,
                         ids=[f"{p}-{i}" for i, (p, _c) in enumerate(_SEMA)])
def test_sema_cells_unchanged(program: str, cells: tuple[str, ...],
                              sweep_dir: Path) -> None:
    sweep = _load()
    want = _by_program()[program]
    got = sweep.survey_sema(
        program, sweep_dir,
        [tuple(c.rsplit("__", 2)[-2:]) for c in cells])
    _fail(want, got, cells)


def test_every_committed_cell_has_a_program() -> None:
    """No committed cell is orphaned by a change to the program split."""
    sweep = _load()
    assert set(_by_program()) <= set(n for n, *_ in sweep.programs())


def test_generator_and_table_agree() -> None:
    """The generator and the committed table describe the SAME matrix.

    Every other test here is parametrized off the table, so an emptied or
    truncated table would yield no items and pass. This is the check that
    cannot: it compiles nothing and compares the two descriptions of the
    matrix directly.

    Equality is not the claim, because the table deliberately omits the rows
    the free-function column answers with a sema error (a source that cannot
    produce the slot's type at all). What IS claimed: no committed cell is
    unknown to the generator; a row is omitted WHOLE or not at all, so a
    truncated table fails; and every slot, source and column the generator
    defines is still represented, so an emptied or gutted one fails.
    """
    sweep = _load()
    generated = sweep.all_cells()
    committed = _expected()
    assert not (set(committed) - set(generated)), (
        "the table names cells the generator no longer produces -- rerun the "
        "script with --update and say why in the commit")

    rows = defaultdict(set)
    for cell in generated:
        rows[cell.rsplit("__", 1)[0]].add(cell)
    partial = sorted(r for r, cells in rows.items()
                     if cells & set(committed) and cells - set(committed))
    assert not partial, (
        f"these rows are only partly in the table, so it has been truncated "
        f"rather than pruned: {partial[:10]}")

    def axes(cells):
        slots, sources, families = set(), set(), set()
        for c in cells:
            slot, half, source, family = c.rsplit("__", 3)
            slots.add(f"{slot}__{half}")
            sources.add(source)
            families.add(family)
        return slots, sources, families

    want, got = axes(generated), axes(committed)
    for kind, w, g in zip(("slot", "source", "column"), want, got):
        assert w == g, f"the table covers no {kind}: {sorted(w - g)}"
