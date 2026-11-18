# Owned steps a user generator declares, held whole or named: a named
# generator object hands over what its function declares, as the direct
# call does; a whole `Own[tuple]` step binds owned, written or read
# (appending it, or an owned element of one, is
# BUGS.md#owned-tuple-step-loop-var-append-rejects); a tuple of owned cells
# is written through, then the source.
from typing import Iterator
from tpy import int32, Own
from fixtures import Cell


def cell_pairs(cs: list[Cell]) -> Iterator[tuple[Own[Cell], int32]]:
    for c in cs:
        yield (Cell(c.v), c.v)


def consume(p: tuple[Own[Cell], int32], kept: list[Cell]) -> None:
    c, n = p
    kept.append(c)


def named_generator() -> None:
    cs = [Cell(1), Cell(2)]
    kept: list[Cell] = []
    for p in cell_pairs(cs):  # tpyc: ok
        consume(p, kept)  # the direct call's step moves into the sink
    it = cell_pairs(cs)
    for p in it:  # tpyc: ok
        consume(p, kept)  # so does a named generator object's
    cs[0].v = 9
    kept[0].v += 10
    print("named generator", [k.v for k in kept], cs[0].v)


def labels(ss: list[str]) -> Iterator[Own[tuple[str, int32]]]:
    for s in ss:
        yield (s + "!", len(s))


def whole_tuple() -> None:
    ss = ["a", "bc"]
    seen: list[str] = []
    for t in labels(ss):  # tpyc: ok
        t = (t[0] + "?", t[1])  # writes the variable
        seen.append(t[0])
    n = 0
    for t in labels(ss):  # tpyc: ok
        n += t[1]  # only reads the variable
    ss[0] = "z"
    print("whole tuple", n, seen, ss)


def twin_cells(cs: list[Cell]) -> Iterator[tuple[Own[Cell], Own[Cell]]]:
    for c in cs:
        yield (Cell(c.v), Cell(c.v + 1))


def owned_cells() -> None:
    cs = [Cell(1), Cell(3)]
    seen: list[int32] = []
    for t in twin_cells(cs):  # tpyc: ok
        t[0].v += 100  # writes the owned element, never the source
        seen.append(t[0].v + t[1].v)
    cs[0].v = 7
    print("owned cells", seen, cs[0].v, cs[1].v)


def main() -> None:
    named_generator()
    whole_tuple()
    owned_cells()
