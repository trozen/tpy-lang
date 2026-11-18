# copy_iter over value, str, tuple, dict-values, readonly and generic element
# sources; a sub-protocol of Iterator takes it (it is an iterator); a typed
# list constructor and a list literal source.
from typing import Iterable, Iterator, Protocol
from tpy import int32, Own, copy_iter, readonly
from fixtures import Cell, take


class Steps[T](Iterator[T], Protocol):
    pass


def total(s: Steps[int32]) -> int32:
    t = 0
    for x in s:
        t += x
    return t


def sum_owned(xs: Iterator[Own[Cell]]) -> int32:
    total = 0
    for c in xs:
        c.v += 1
        total += c.v
    return total


def fwd(xs: Iterator[Cell]) -> int32:
    # copy_iter of a lending iterator parameter at an owning Iterator slot
    return sum_owned(copy_iter(xs))  # tpyc: ok


def str_lens(ss: list[str]) -> int32:
    # a sync str loop over copy_iter refers to each step's own copy
    n = 0
    for s in copy_iter(ss):  # tpyc: ok
        n += len(s)
        print("sync str loop", s)
    return n


def own_str_lens(source: Iterable[Own[str]]) -> int32:
    # an owning str slot: a lent list's steps and copy_iter's copies alike
    n = 0
    for s in source:
        n += len(s)
    return n


def ro_copies(cells: readonly[list[Cell]]) -> Own[list[Cell]]:
    # a readonly source: copy_iter only reads it
    return take(copy_iter(cells))  # tpyc: ok


def h[T](xs: list[T]) -> Own[list[T]]:
    # a generic element: the copy is whatever T's copy is
    return list(copy_iter(xs))  # tpyc: ok


def main() -> None:
    ns = [3, 1, 2]
    print("value", sorted(copy_iter(ns)), total(copy_iter(ns)))  # tpyc: ok
    words = ["b", "a"]
    print("str", list(copy_iter(words)))  # tpyc: ok
    pairs = [(Cell(1), 2)]
    tp = list(copy_iter(pairs))  # tpyc: ok
    tp[0][0].v = 9
    print("tuple", tp[0][0].v, pairs[0][0].v)
    d = {1: Cell(4)}
    dv = list(copy_iter(d.values()))  # tpyc: ok
    dv[0].v = 40
    print("dict values", dv[0].v, d[1].v)
    cells = [Cell(5)]
    rc = ro_copies(cells)
    rc[0].v = 50
    print("readonly", rc[0].v, cells[0].v)
    hc = h(cells)
    hc[0].v = 51
    print("generic", hc[0].v, cells[0].v, h([7, 8]))
    typed = list[Cell](copy_iter(cells))  # tpyc: ok
    typed[0].v = 52
    print("typed ctor", typed[0].v, cells[0].v)
    # a named copy_iter at an `Iterator[Own[Cell]]` slot twice: the second
    # pass is empty, as over any one-pass iterator
    ci = copy_iter(cells)
    print("iterator slot", sum_owned(ci), sum_owned(ci), cells[0].v)  # tpyc: ok
    print("iterator param", fwd(iter(cells)), cells[0].v)
    words2 = ["ab", "c"]
    n = str_lens(words2)
    words2[0] = "zz"
    print("sync str after", n, words2)
    lent = ["de", "f"]
    print("own str lent", own_str_lens(lent))  # tpyc: ok
    lent[1] = "gh"
    print("own str lent after", lent)
    print("own str copied", own_str_lens(copy_iter(lent)), lent)  # tpyc: ok
    for x in copy_iter([1, 2, 3]):  # tpyc: ok
        print("literal", x)
    lit = list(copy_iter([Cell(6), Cell(7)]))  # tpyc: ok
    print("literal list", lit[1].v)
