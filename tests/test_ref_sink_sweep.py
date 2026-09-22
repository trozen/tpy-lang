"""The committed SINK x source x reference-kind x position verdict table.

The generator, the survey and the table live in
`scripts/thir_migration/review/ref_sink_sweep.py` -- see its docstring for what
a cell records (a compile verdict, the first warning and the lowered subject
statement, nothing about behaviour) and for why this is an instrument rather
than an arm pin. This module is the gate over it: it surveys every cell and
fails on any whose verdict, warning or render moved, so a change to what one
sink admits where its siblings do not -- or to whether a sink COPIES the source
where its twin binds a pointer -- is a diff in the committed table rather than
something the next review has to find.

One test per PROGRAM, not per cell. A program holds one (kind, position)
pair's whole sink x source set -- each cell its own body -- and the THIR survey
answers all of them from one compilation. The cells that cost a recompilation
(a sema error, and a rejecting FRAME in the generator / async positions, which
ends the pass where it stands) are handed to the survey so it leaves them out,
and checked separately in minimal one-cell programs.

It lives beside the case suite rather than under `tpyc/` because it compiles a
generated corpus rather than pinning an arm, which is where the arg-family and
property sweeps live for the same reason. The sdist ships `tpyc/` without
`scripts/`, so the script it drives is not always there -- hence the lazy load
and the skip when it is absent.
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
_SCRIPT = _REVIEW / "ref_sink_sweep.py"

pytestmark = pytest.mark.skipif(
    not _SCRIPT.exists(),
    reason="scripts/thir_migration/review is not part of this checkout")

_sweep = None


def _load():
    """Import the script on first use, never at collection."""
    global _sweep
    if _sweep is None:
        spec = importlib.util.spec_from_file_location("ref_sink_sweep",
                                                      _SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("ref_sink_sweep", mod)
        spec.loader.exec_module(mod)
        _sweep = mod
    return _sweep


def _expected() -> dict[str, list[str]]:
    if not _SCRIPT.exists():
        return {}
    return json.loads((_REVIEW / "ref_sink_sweep.expected.json").read_text())


def _by_program() -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = defaultdict(dict)
    sweep = _load()
    for cell, value in _expected().items():
        out[sweep.program_of(cell)][cell] = value
    return dict(out)


_PROGRAMS = sorted(_by_program()) if _SCRIPT.exists() else []


def _solo(program: str, want: dict[str, list[str]]) -> list[str]:
    """The cells of `program` that cost a recompilation of their own.

    A sema error stops the module at its first one, and an ICE aborts the
    whole compilation (the survey then halves the program to find it). A
    reject inside a FRAME (the generator and async positions) ends the
    emission pass where it stands, so it would take every later cell in the
    program with it -- the same reason the property sweep gives a frame cell
    its own program.
    """
    sweep = _load()
    frame = sweep.POSITION_BY_NAME[program.split("__")[1]].frame
    return sorted(c for c in want
                  if want[c][0].startswith(("sema:", "crash:"))
                  or (frame and want[c][0].startswith("reject:")))


# One MINIMAL program per cell, so an item carries a few seconds of work
# rather than a whole position's worth.
_SOLO_CHUNK = 6


def _solo_chunks() -> list[tuple[str, tuple[str, ...]]]:
    out = []
    for program, want in sorted(_by_program().items()):
        solo = _solo(program, want)
        for i in range(0, len(solo), _SOLO_CHUNK):
            out.append((program, tuple(solo[i:i + _SOLO_CHUNK])))
    return out


_SOLO = _solo_chunks() if _SCRIPT.exists() else []


@pytest.fixture(scope="session")
def sweep_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("ref_sink_sweep")


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
    solo = set(_solo(program, want))
    cells = sorted({sweep.split_cell(c) for c in want})
    got = sweep.survey(program, sweep_dir, cells=cells,
                       expect_solo=sorted({sweep.split_cell(c)
                                           for c in solo}))
    _fail(want, got, set(want) - solo)


@pytest.mark.parametrize("program,cells", _SOLO,
                         ids=[f"{p}-{i}" for i, (p, _c) in enumerate(_SOLO)])
def test_solo_cells_unchanged(program: str, cells: tuple[str, ...],
                              sweep_dir: Path) -> None:
    sweep = _load()
    want = _by_program()[program]
    got = sweep.survey_solo(program, sweep_dir,
                            [sweep.split_cell(c) for c in cells])
    _fail(want, got, cells)


def test_every_committed_cell_has_a_program() -> None:
    """No committed cell is orphaned by a change to the program split."""
    sweep = _load()
    assert set(_by_program()) <= {n for n, *_ in sweep.programs()}


def test_generator_and_table_agree() -> None:
    """The generator and the committed table describe the SAME matrix.

    Every other test here is parametrized off the table, so an emptied or
    truncated table would yield no items and pass. This is the check that
    cannot: it compiles nothing and compares the two descriptions directly.

    Equality is not the claim, because the table deliberately omits the rows
    `decl_alias` answers with a sema error (a source that cannot produce the
    kind at all). What IS claimed: no committed cell is unknown to the
    generator; a row is omitted WHOLE or not at all, so a truncated table
    fails; and every kind, position, source and sink the generator defines is
    still represented, so an emptied or gutted one fails.
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
        kinds, positions, sources, sinks = set(), set(), set(), set()
        for c in cells:
            kind, position, source, sink = c.split("__")
            kinds.add(kind)
            positions.add(position)
            sources.add(source)
            sinks.add(sink)
        return kinds, positions, sources, sinks

    want, got = axes(generated), axes(committed)
    for kind, w, g in zip(("kind", "position", "source", "sink"), want, got):
        assert w == g, f"the table covers no {kind}: {sorted(w - g)}"
