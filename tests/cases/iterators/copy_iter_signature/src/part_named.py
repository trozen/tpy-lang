# copy_iter results held in names: a local's declared `Iterator[Own[Cell]]`
# hands its copies over, through an alias and a capture too; an alias of a
# lending `iter(...)` under a shadowing inner binding still warns; a parameter
# named like it forwards its argument; an owning parameter copied
# rather than consumed; copy_iter over a generator of a temporary.
from typing import Iterable, Iterator
from tpy import int32, Own, copy_iter
from fixtures import Cell, take


class View:
    cells: list[Cell]

    def __init__(self) -> None:
        self.cells = [Cell(1), Cell(2)]

    def __iter__(self) -> Iterator[Cell]:
        return iter(self.cells)


def passthru[T](src: Iterable[T]) -> Iterator[T]:
    for x in src:
        yield x


def total(xs: Iterable[Own[Cell]]) -> int32:
    t = 0
    for c in xs:
        c.v += 10
        t += c.v
    return t


def forward(it2: Iterable[Own[Cell]]) -> int32:
    return total(it2)  # tpyc: ok


def copies_param(p: Own[list[Cell]]) -> int32:  # tpyc: warning(/param 'p' is never consumed/)
    # copy_iter borrows its argument, so the owning parameter stays unused
    return len(list(copy_iter(p)))


def main() -> None:
    cells = [Cell(1), Cell(2)]
    # an alias of a named copy_iter hands the copies over: its type says so
    it = copy_iter(cells)
    jt = it
    print("alias", len(take(jt)), len(take(jt)), cells[0].v)  # tpyc: ok
    # a closure capturing the named copy_iter, the same
    ct = copy_iter(cells)

    def grab() -> int32:
        return total(ct)  # tpyc: ok

    print("capture", grab(), grab(), cells[0].v)
    # a captured alias whose name an inner copy_iter binding shadows: the
    # alias still lends the outer `iter(...)` steps, so it warns
    st = iter(cells)
    sj = st

    def shadow() -> int32:
        st = copy_iter(cells)
        inner = total(st)  # tpyc: ok
        return inner + total(sj)  # tpyc: warning(/copies Cell elements/)

    print("shadowed capture", shadow() > 0)
    # a parameter named like the copy_iter local hands over by its own
    # declared type: the owning parameter is forwarded as it is
    fresh = [Cell(1), Cell(2)]
    it2 = copy_iter(fresh)
    print("param", forward(it2), fresh[0].v)  # tpyc: ok
    print("never consumed", copies_param([Cell(3)]))
    # copy_iter over a generator of a temporary
    print("temp generator", total(copy_iter(passthru(View()))))  # tpyc: ok
