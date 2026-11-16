# Which `__iter__` / `__next__` declares the step: the one iteration runs
# (callable with no argument, a defaulted parameter included), and only
# the returned iterator's own declaration hands a step over -- an `Own`
# type argument on a lending `SpanIter` does not.
from typing import Iterable, Iterator, Self
from tpy import Own, SpanIter, dispatch, int32
from tplib.array_list import ArrayList


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


G = Cell(7)


class ArgBag:
    @dispatch
    def __iter__(self, mode: int32) -> Iterator[Own[Cell]]:
        yield Cell(mode)

    @dispatch
    def __iter__(self) -> Iterator[Cell]:
        yield G


class SpanBag:
    cells: ArrayList[Cell, 4]

    def __init__(self) -> None:
        self.cells = ArrayList[Cell, 4]()
        self.cells.append(Cell(7))

    def __iter__(self) -> SpanIter[Own[Cell]]:
        return self.cells.__iter__()


class DefaultIter:
    def __iter__(self, ignored: int32 = 0) -> Iterator[Cell]:
        yield G


class DefaultNext:
    done: bool

    def __init__(self) -> None:
        self.done = False

    def __iter__(self) -> Self:
        return self

    def __next__(self, ignored: int32 = 0) -> Cell:
        if self.done:
            raise StopIteration
        self.done = True
        return G


class Pt:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class TupleSpan:
    rows: ArrayList[tuple[Pt, int32], 4]

    def __init__(self) -> None:
        self.rows = ArrayList[tuple[Pt, int32], 4]()
        self.rows.append((Pt(7), 1))

    def __iter__(self) -> SpanIter[tuple[Pt, int32]]:
        return self.rows.__iter__()


def take(xs: Iterable[Own[Cell]]) -> int32:
    for x in xs:
        x.v += 1
        return x.v
    return 0


def take_rows(xs: Iterable[Own[tuple[Pt, int32]]]) -> int32:
    for t in xs:
        t[0].x += 1
        return t[0].x + t[1]
    return 0


def main() -> None:
    # the argument-less `__iter__` lends, whatever the argument-taking
    # overload declares
    G.v = 7
    print("step member required arg", take(ArgBag()), G.v)  # tpyc: warning(/copies Cell elements/)
    xs = list(ArgBag())  # tpyc: warning(/copies Cell elements/)
    xs[0].v += 10
    print("step member required arg list()", xs[0].v, G.v)
    # an `Own` type argument on a lending native iterator hands nothing over
    sb = SpanBag()
    print("step member SpanIter Own arg", take(sb), sb.cells[0].v)  # tpyc: warning(/copies Cell elements/)
    # a defaulted parameter leaves `__iter__` / `__next__` the step
    G.v = 7
    print("step member defaulted __iter__", take(DefaultIter()), G.v)  # tpyc: warning(/copies Cell elements/)
    G.v = 7
    print("step member defaulted __next__", take(DefaultNext()), G.v)  # tpyc: warning(/copies Cell elements/)
    # a returned `SpanIter` steps the tuple its declaration writes
    ts = TupleSpan()
    n = take_rows(ts)  # tpyc: warning(/copies tuple\[Pt, int32\] elements/)
    row = ts.rows[0]
    print("step member SpanIter tuple", n, row[0].x)
