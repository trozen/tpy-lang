# Reseating a pointer-repr Optional local: two rvalue reseats reuse the same
# hidden slot, and an lvalue reseat from a field aliases it -- so mutating
# through the reseated name is visible on the record it came from. The
# lvalue sources a plain pointer local reseats from re-point an Optional
# local too: a parameter, `self`, a borrow call, a dunder result, a call
# ternary.
from typing import Iterator
from tpy import int32, Own


class Inner:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value


class Box:
    inner: Inner

    def __init__(self, inner: Own[Inner]) -> None:
        self.inner = inner


class Cell:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value

    def __add__(self, other: "Cell") -> "Cell":
        return self if self.value > other.value else other

    def __neg__(self) -> "Cell":
        return self

    def keep(self) -> int32:
        # a method: the Optional local re-points at the receiver
        saved: Cell | None = None
        saved = self  # tpyc: ok
        if saved is not None:
            saved.value = 7
        return self.value


def param_reseat(a: Cell | None, b: Cell) -> int32:
    # a free function: the Optional local re-points at a record parameter
    current = a
    current = b  # tpyc: ok
    if current is not None:
        current.value = 5
    return b.value


def double_rvalue_reseat() -> int32:
    p: Inner | None
    p = Inner(1)
    p = Inner(2)  # the second reseat reuses the first's slot
    if p is not None:
        return p.value
    return 0


def lvalue_reseat(b: Box) -> int32:
    p: Inner | None = None
    p = b.inner  # an lvalue reseat -- an alias, not a copy
    if p is not None:
        p.value = 9
    return b.inner.value


def pick(c: Cell) -> Cell:
    return c


def borrow_call(a: Cell) -> int32:
    # a borrow-returning call re-points the Optional local at its result
    p: Cell | None = None
    p = pick(a)  # tpyc: ok
    if p is not None:
        p.value = 11
    return a.value


def dunder() -> int32:
    # a borrow-returning dunder's result is an operand: the local aliases
    # it, so a write to the operand shows through the local
    a = Cell(5)
    b = Cell(4)
    p: Cell | None = None
    p = a + b  # tpyc: ok
    a.value = 12
    first = 0 if p is None else p.value
    p = -b  # tpyc: ok
    b.value = 13
    return first * 100 + (0 if p is None else p.value)


def dunder_gen(a: Cell, b: Cell) -> Iterator[int32]:
    # a generator: the dunder's result (a `const Cell*` over the unmutated
    # parameters) is held across a suspension and still aliases its operand
    p: Cell | None = None
    yield 1
    p = a + b  # tpyc: ok
    yield 2
    a.value = 14
    if p is not None:
        yield p.value


def call_ternary(a: Cell, b: Cell, flag: bool) -> int32:
    # a ternary of borrow-returning calls re-points the local at an arm
    p: Cell | None = None
    p = pick(a) if flag else pick(b)  # tpyc: ok
    if p is not None:
        p.value = 15
    return a.value * 100 + b.value


def main() -> None:
    print(double_rvalue_reseat(), lvalue_reseat(Box(Inner(3))))
    c = Cell(1)
    print("self reseat", c.keep(), c.value)
    b = Cell(2)
    print("param reseat", param_reseat(None, b), b.value)
    print("borrow call", borrow_call(Cell(3)))
    print("dunder", dunder())
    print("dunder gen", list(dunder_gen(Cell(5), Cell(4))))
    print("call ternary", call_ternary(Cell(6), Cell(7), False))


main()
