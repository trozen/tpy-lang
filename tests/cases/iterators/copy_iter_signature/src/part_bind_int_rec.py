# The `int` (arbitrary precision) and record twins of part_bind_values: a
# loop variable over an owned step owns it whatever the body does. A record
# variable rebound through `nonlocal` is refused (a reference local), so its
# nested-def section writes a field instead.
from typing import Iterator
from tpy import int32, Own, copy_iter
from fixtures import Cell


def fresh_ints(xs: list[int]) -> Iterator[Own[int]]:
    for x in xs:
        yield x + 1


def fresh_cells(cs: list[Cell]) -> Iterator[Own[Cell]]:
    for c in cs:
        yield Cell(c.v)


def ints() -> None:
    xs: list[int] = [1, 2]
    kept: list[int] = []
    for x in copy_iter(xs):  # tpyc: ok
        kept.append(x)  # consumes the variable
    for x in fresh_ints(xs):  # tpyc: ok
        kept.append(x)
    for x in copy_iter(xs):  # tpyc: ok
        x += 10  # writes the variable
        kept.append(x)
    for x in fresh_ints(xs):  # tpyc: ok
        x += 10
        kept.append(x)
    n: int = 0
    for x in copy_iter(xs):  # tpyc: ok
        n += x  # only reads the variable
    for x in fresh_ints(xs):  # tpyc: ok
        n += x
    xs[0] = 7
    print("bind int", n, kept, xs)


def ints_nested() -> None:
    # its own function, as cells_nested below
    xs: list[int] = [1, 2]
    kept: list[int] = []
    for x in copy_iter(xs):  # tpyc: ok
        def bump() -> None:
            nonlocal x
            x = x + 100  # a nested def rebinds the variable
        bump()
        kept.append(x)
    for x in fresh_ints(xs):  # tpyc: ok
        def bump2() -> None:
            nonlocal x
            x = x + 100
        bump2()
        kept.append(x)
    xs[0] = 7
    print("bind int nested", kept, xs)


def cells() -> None:
    cs = [Cell(1), Cell(2)]
    kept: list[Cell] = []
    for c in copy_iter(cs):  # tpyc: ok
        kept.append(c)  # consumes the variable
    for c in fresh_cells(cs):  # tpyc: ok
        kept.append(c)
    for c in copy_iter(cs):  # tpyc: ok
        c.v += 10  # writes the variable
        kept.append(c)
    for c in fresh_cells(cs):  # tpyc: ok
        c.v += 10
        kept.append(c)
    n = 0
    for c in copy_iter(cs):  # tpyc: ok
        n += c.v  # only reads the variable
    for c in fresh_cells(cs):  # tpyc: ok
        n += c.v
    cs[0].v = 7
    print("bind cell", n, [k.v for k in kept], cs[0].v, cs[1].v)


def cells_nested() -> None:
    # its own function: a variable a nested def captures is never moved
    # out, so the consuming sections above would copy under that name
    cs = [Cell(1), Cell(2)]
    kept: list[Cell] = []
    for c in copy_iter(cs):  # tpyc: ok
        def bump() -> None:
            c.v += 100  # a nested def writes through the variable
        bump()
        kept.append(c)  # tpyc: warning(/copies Cell into owned storage/)
    for c in fresh_cells(cs):  # tpyc: ok
        def bump2() -> None:
            c.v += 100
        bump2()
        kept.append(c)  # tpyc: warning(/copies Cell into owned storage/)
    cs[0].v = 7
    print("bind cell nested", [k.v for k in kept], cs[0].v, cs[1].v)


def main() -> None:
    ints()
    ints_nested()
    cells()
    cells_nested()
