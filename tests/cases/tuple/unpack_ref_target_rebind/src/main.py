# Rebinding a borrowed tuple-unpack target rebinds the NAME, like the scalar
# alias `x = p; x = q`: the target is a pointer local, so the element's source
# keeps its value and later writes reach the new referent (CPython aliases).
from typing import Iterator

from tpy import Own, ReturnException, error_return, int32, nocopy


class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Stop(Exception, ReturnException):
    pass


@nocopy
class Tok:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def both(p: Cell, q: Cell) -> tuple[Cell, Cell]:
    return (p, q)


def split(p: Cell) -> tuple[Cell, Own[Cell]]:
    return (p, Cell(p.n + 100))


def one(b: Cell) -> tuple[Cell, int32]:
    return (b, 7)


def tok_pair(a: Tok) -> tuple[Tok, int32]:
    return (a, 1)


def cells(a: Cell, b: Cell) -> Iterator[tuple[Cell, int32]]:
    yield (a, 1)
    yield (b, 2)


# free function, call source, rebind to another reference.
def to_name() -> None:
    p = Cell(1)
    q = Cell(8)
    x, y = both(p, q)
    x = q  # tpyc: ok
    x.n = 80
    print("to_name", p.n, q.n, y.n)


# free function, rebind to a fresh object: it takes storage of its own.
def to_fresh() -> None:
    b = Cell(1)
    x, k = one(b)
    x = Cell(5)  # tpyc: ok
    x.n = 6
    print("to_fresh", b.n, x.n, k)


# mixed borrow + Own result: only the borrowed target is re-pointed.
def mixed() -> None:
    p = Cell(1)
    q = Cell(8)
    ref, owned = split(p)
    ref = q  # tpyc: ok
    ref.n = 80
    print("mixed", p.n, q.n, owned.n)


# tuple param source: a write before the rebind reaches the element, one
# after it reaches the new referent.
def param_src(t: tuple[Cell, int32], q: Cell) -> None:
    x, k = t
    x.n = 70
    x = q  # tpyc: ok
    x.n = 80
    print("param_src", t[0].n, q.n, k)


# rebind under a branch, write after the join.
def branch(b: Cell, q: Cell, c: bool) -> None:
    x, k = one(b)
    if c:
        x = q  # tpyc: ok
    x.n = 83
    print("branch", b.n, q.n)


# rebind inside a loop: the first write reaches the element, later ones q.
def loop(b: Cell, q: Cell) -> None:
    x, k = one(b)
    i = 0
    while i < 2:
        x.n = 84 + i
        x = q  # tpyc: ok
        i += 1
    print("loop", b.n, q.n)


# a closure reads the rebound name.
def closure(b: Cell, q: Cell) -> None:
    x, k = one(b)
    x = q  # tpyc: ok

    def show() -> int32:
        return x.n

    x.n = 85
    print("closure", b.n, show())


class Holder:
    k: int32

    def __init__(self) -> None:
        self.k = 0

    # method body.
    def rebind(self, b: Cell, q: Cell) -> None:
        x, k = one(b)
        x = q  # tpyc: ok
        x.n = 86
        print("method", b.n, q.n)


# for-each head over zip: the loop target is re-pointed per iteration.
def zip_head(q: Cell) -> None:
    cs = [Cell(1), Cell(2)]
    ns = [10, 20]
    for c, n in zip(cs, ns):
        c = q  # tpyc: ok
        c.n = n
    print("zip_head", cs[0].n, cs[1].n, q.n)


# for-each head over a generator yielding borrowed tuples.
def gen_head(q: Cell) -> None:
    a = Cell(1)
    b = Cell(2)
    for c, n in cells(a, b):
        c.n = 5
        c = q  # tpyc: ok
        c.n = n
    print("gen_head", a.n, b.n, q.n)


# @error_return body.
@error_return(Stop)
def er_body(b: Cell, q: Cell) -> int32:
    x, k = one(b)
    x = q  # tpyc: ok
    x.n = 87
    print("er_body", b.n, q.n)
    return k


# match arm body.
def match_arm(b: Cell, q: Cell, tag: int32) -> None:
    match tag:
        case 1:
            x, k = one(b)
            x = q  # tpyc: ok
            x.n = 88
        case _:
            pass
    print("match_arm", b.n, q.n)


# try body with an except handler.
def try_except(b: Cell, q: Cell) -> None:
    try:
        x, k = one(b)
        x = q  # tpyc: ok
        x.n = 89
    except ValueError:
        pass
    print("try_except", b.n, q.n)


# two loops binding the same name: only the loop whose body rebinds `c`
# re-points it; the first still writes through to the list element.
def two_loops() -> None:
    cs = [Cell(1)]
    for c, n in zip(cs, [1]):
        c.n = 5
    for c, n in zip(cs, [2]):
        c = Cell(9)  # tpyc: ok
        c.n = 6
    print("two_loops", cs[0].n)


# a tuple param written ONLY through the rebound name: the rebind keeps the
# source mutable, as the scalar alias's does, so the element pointer binds.
def unwritten_param(t: tuple[Cell, int32], q: Cell) -> None:
    x, k = t
    x = q  # tpyc: ok
    x.n = 81
    print("unwritten_param", t[0].n, q.n)


# the same over a zip head off a list param: only the list the rebound
# element comes from has to stay mutable.
def unwritten_zip(cs: list[Cell], ds: list[Cell], q: Cell) -> None:
    for c, d in zip(cs, ds):
        c = q  # tpyc: ok
        c.n = 82
    print("unwritten_zip", cs[0].n, ds[0].n, q.n)


class Ctx:
    c: Cell

    def __init__(self, n: int32) -> None:
        self.c = Cell(n)

    def __enter__(self) -> Cell:
        return self.c

    def __exit__(self, kind, value, tb) -> None:
        pass


# context manager: `with ... as x` rebinds the unpack target.
def with_as(b: Cell, ctx: Ctx) -> None:
    x, k = one(b)
    with ctx as x:  # tpyc: ok
        x.n = 80
    print("with_as", b.n, ctx.c.n)


class Built:
    m: int32

    # constructor body.
    def __init__(self, b: Cell, q: Cell) -> None:
        x, k = one(b)
        x = q  # tpyc: ok
        x.n = 80
        self.m = b.n


# swap: both targets are re-pointed, neither referent is written.
def swap(p: Cell, q: Cell) -> None:
    x, y = both(p, q)
    x, y = y, x  # tpyc: ok
    x.n = 80
    print("swap", p.n, q.n, y.n)


# for heads: enumerate, dict.items() and a list of tuples.
def enum_head(q: Cell) -> None:
    cs = [Cell(1), Cell(2)]
    for i, c in enumerate(cs):
        c = q  # tpyc: ok
        c.n = 50 + i
    print("enum_head", cs[0].n, cs[1].n, q.n)


def items_head(q: Cell) -> None:
    d = {"a": Cell(1)}
    for key, c in d.items():
        c = q  # tpyc: ok
        c.n = 50
    print("items_head", d["a"].n, q.n)


def list_tuple_head(q: Cell) -> None:
    ts = [(Cell(1), 1)]
    for c, k in ts:
        c = q  # tpyc: ok
        c.n = 50
    print("list_tuple_head", ts[0][0].n, q.n)


# conditional rebind in a zip head: writes before it reach the element.
def cond_head(q: Cell) -> None:
    cs = [Cell(1), Cell(2)]
    for c, n in zip(cs, [1, 2]):
        c.n = 10 + n
        if n == 1:
            c = q  # tpyc: ok
        c.n = 20 + n
    print("cond_head", cs[0].n, cs[1].n, q.n)


# closure late binding: the rebind after the def is what the call sees.
def late_closure(b: Cell, q: Cell) -> None:
    x, k = one(b)

    def show() -> int32:
        return x.n

    x = q  # tpyc: ok
    x.n = 85
    print("late_closure", b.n, show())


# @nocopy element: a write-through would be a copy-assign, which cannot exist.
def nocopy_target() -> None:
    a = Tok(1)
    other = Tok(2)
    x, k = tok_pair(a)
    x = other  # tpyc: ok
    x.n = 9
    print("nocopy", a.n, other.n)


# inverse: a target that is never rebound stays a plain alias of the element.
def not_rebound() -> None:
    b = Cell(1)
    x, k = one(b)
    x.n = 42
    print("not_rebound", b.n, k)


def main() -> None:
    to_name()
    to_fresh()
    mixed()
    param_src((Cell(1), 2), Cell(8))
    branch(Cell(1), Cell(8), True)
    loop(Cell(1), Cell(8))
    closure(Cell(1), Cell(8))
    h = Holder()
    hb = Cell(1)
    hq = Cell(8)
    h.rebind(hb, hq)
    zip_head(Cell(8))
    gen_head(Cell(8))
    try:
        er_body(Cell(1), Cell(8))
    except Stop:
        pass
    match_arm(Cell(1), Cell(8), 1)
    try_except(Cell(1), Cell(8))
    two_loops()
    unwritten_param((Cell(1), 2), Cell(8))
    unwritten_zip([Cell(1)], [Cell(2)], Cell(8))
    with_as(Cell(1), Ctx(8))
    bb = Cell(1)
    bq = Cell(8)
    built = Built(bb, bq)
    print("ctor", built.m, bq.n)
    swap(Cell(1), Cell(8))
    enum_head(Cell(3))
    items_head(Cell(3))
    list_tuple_head(Cell(3))
    cond_head(Cell(3))
    late_closure(Cell(1), Cell(8))
    nocopy_target()
    not_rebound()


main()
