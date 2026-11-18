# A value hands its steps over by its own type: a cursor whose `__next__`
# returns `Own[...]` keeps doing so through a generic identity call; a named
# owning generator passed as an argument hands over as its call does; an
# alias of an owning local passes the container, never moves it; a bounded
# type parameter's value hands over as its bound declares, as its concrete twin.
from typing import Iterable, Iterator
from tpy import int32, Own, nocopy
from fixtures import Cell, take


@nocopy
class Token:
    def __init__(self, v: int32) -> None:
        self.v = v


class Cursor:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __iter__(self) -> "Cursor":
        return self

    def __next__(self) -> Own[Token]:
        if self.n == 0:
            raise StopIteration
        self.n -= 1
        return Token(self.n)


def identity[T](v: T) -> T:
    return v


def keep_tokens(xs: Iterable[Own[Token]]) -> Own[list[Token]]:
    out: list[Token] = []
    for t in xs:
        out.append(t)
    return out


def collect[I: Iterable[Own[Token]]](p: I) -> Own[list[Token]]:
    out: list[Token] = []
    for t in p:
        # the bound's `Own[Token]` steps move in, as in keep_tokens
        out.append(t)  # tpyc: ok
    return out


def grab[I: Iterable[Own[Cell]]](p: I) -> Own[list[Cell]]:
    out: list[Cell] = []
    for c in p:
        # the bound's `Own[Cell]` steps move in, as in take
        out.append(c)  # tpyc: ok
    return out


class CellCursor:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __iter__(self) -> "CellCursor":
        return self

    def __next__(self) -> Own[Cell]:
        if self.n == 0:
            raise StopIteration
        self.n -= 1
        return Cell(self.n)


def pairs(xs: list[Cell]) -> Iterator[tuple[Own[Cell], int32]]:
    for x in xs:
        yield (Cell(x.v), x.v)


def count_pairs(src: Iterable[tuple[Own[Cell], int32]]) -> int32:
    n = 0
    for p in src:
        p[0].v += 100
        n += p[0].v + p[1]
    return n


def mk() -> Own[list[Cell]]:
    return [Cell(1), Cell(2)]


def main() -> None:
    # the identity call's `T` is the cursor, whose steps are `Own[Token]`
    got = keep_tokens(identity(Cursor(3)))  # tpyc: ok
    print("identity cursor", len(got), got[0].v)
    cs = [Cell(1), Cell(2)]
    direct = count_pairs(pairs(cs))  # tpyc: ok
    pt = pairs(cs)
    # a named owning generator as an argument: as its call above
    named = count_pairs(pt)  # tpyc: ok
    cs[0].v = 9
    print("named pairs argument", direct, named, cs[0].v)
    bt = collect(Cursor(2))  # tpyc: ok
    ct = keep_tokens(Cursor(2))
    print("bounded nocopy", len(bt), bt[1].v, len(ct), ct[1].v)
    bg = grab(CellCursor(2))  # tpyc: ok
    tg = take(CellCursor(2))
    bg[0].v = 80
    print("bounded copyable", len(bg), bg[0].v, bg[1].v, len(tg), tg[1].v)
    xs = mk()
    ys = xs
    # the alias lends the owning local's elements, so they are copied
    k = take(ys)  # tpyc: warning(/copies Cell elements/)
    print("owning local alias", len(k), len(xs))
