# Each value of the binding contract (`result=` / `args=` on @native and
# @cpp_template) on a user binding, with what the compiler does with it.
__tpy__ = {"includes": ["native_types.hpp"]}

from typing import Iterable, Iterator
from tpy import int32, int64, Own, NativeIterable, pure, readonly
from tpy.extern import native, cpp_template


@native
class Cell:
    v: int32


@native("::Ring", elements=True)
class Ring[T](NativeIterable[T]):
    def __init__(self) -> None: ...

    @native("tpy::__iter__", function=True)
    @pure
    @readonly
    def __iter__(self) -> Iterator[T]: ...

    @native("push", args={"v": "stored"})
    def push(self, v: Own[T]) -> None: ...

    @native("put", args={"self": "writes"})
    def put(self, i: int32, v: Own[T]) -> None: ...

    @native("has", args={"v": "compared"})
    @pure
    @readonly
    def has(self, v: T) -> bool: ...


@native(result={"a": "borrow", "b": "none"})
def pick_first(a: Cell, b: Cell) -> Cell: ...


@native(result={"a": "borrow", "b": "borrow"})
def pick_either(a: Cell, b: Cell) -> Cell: ...


@native(result={"items": "element"})
def first_of(items: list[Cell]) -> Cell: ...


@cpp_template("::tpy::builtin_enumerate({0})", result={"items": "adopts"})
def walk[T](items: Iterable[T]) -> Iterator[tuple[int32, T]]: ...


def fresh_cells() -> Own[list[Cell]]:
    return [Cell(7), Cell(8)]


# "borrow": the result IS `a`, so a write through it reaches `a`; "none":
# a temporary at `b` lends nothing, so the result still binds as a borrow
def borrow_none() -> None:
    a = Cell(1)
    c = pick_first(a, Cell(9))  # tpyc: ok
    c.v = 5
    print("borrow_none", a.v)


# "borrow" at both: a temporary at `b` may be what the result is, so
# holding the result copies it
def borrow_temp() -> None:
    a = Cell(1)
    c = pick_either(a, Cell(9))  # tpyc: warning(/copies Cell into local 'c'/)
    c.v = 5
    print("borrow_temp", a.v, c.v)


# "element": the result is an element of the list, so a write through it
# reaches the list
def element() -> None:
    cells = [Cell(1), Cell(2)]
    f = first_of(cells)  # tpyc: ok
    f.v = 30
    print("element", cells[0].v)


# "adopts": a fresh list is taken over by the result, which the loop drives
def adopts() -> None:
    for i, c in walk(fresh_cells()):  # tpyc: ok
        print("adopts", i, c.v)


# "writes" on self: `put` replaces an element in place, so the loop's
# iterator over the ring stays valid
def writes() -> None:
    r = Ring[int32]()
    r.push(1)
    r.push(2)
    t = 0
    for v in r:
        r.put(0, 9)  # tpyc: ok
        t += v
    print("writes", t)


# "compared": `has` only compares its argument with the elements, so a
# wider key that narrows into the element type is a lookup that may raise
def compared() -> None:
    r = Ring[int32]()
    r.push(3)
    k: int64 = 3
    print("compared", r.has(k))  # tpyc: warning(/lookup key narrows from int64 to int32/)


# "stored": `list.append` holds its argument, so a list literal whose
# numbers are not decided yet widens to hold it
def stored() -> None:
    big: int64 = 5000000000
    xs = [1, 2]  # tpyc: type(list[int64])
    xs.append(big)
    print("stored", xs[2])


def main() -> None:
    borrow_none()
    borrow_temp()
    element()
    adopts()
    writes()
    compared()
    stored()


main()
