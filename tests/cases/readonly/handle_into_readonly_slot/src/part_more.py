# More consumers of a handle: a loop in an inferred-readonly method, an
# overloaded callee whose winner takes the handle readonly, an
# `auto_readonly[V] | None` result (readonly as a whole off a readonly
# receiver), and fresh bytearray results (mutable whatever their source).
from typing import Iterable
from tpy import int32, readonly, auto_readonly, dispatch, Own


class Tally:
    d: dict[str, int32]

    def __init__(self) -> None:
        self.d = {"a": 1, "b": 2}

    def total(self) -> int32:
        # loop: `total` stays inferred readonly
        n = 0
        for v in self.d.values():  # tpyc: ok
            n += v
        return n


@dispatch
def size(v: readonly[Iterable[int32]]) -> int32:
    n = 0
    for _ in v:
        n += 1
    return n


@dispatch
def size(v: list[int32]) -> int32:
    return len(v)


def via_overload(d: dict[str, int32]) -> int32:
    # overloaded callee: the winner's readonly parameter keeps `d` const
    return size(d.values())  # tpyc: ok


class Cell:
    def __init__(self, v: int32) -> None:
        self.v = v


class Shelf:
    c: Cell | None

    def __init__(self) -> None:
        self.c = Cell(4)

    @auto_readonly
    def find(self) -> auto_readonly[Cell] | None:
        # the marker covers the Optional as a whole
        return self.c


def peek(s: readonly[Shelf]) -> int32:
    c = s.find()  # tpyc: type(/readonly\[Cell \| None\]/)
    return c.v if c is not None else -1


def bump(s: Shelf) -> int32:
    c = s.find()
    if c is not None:
        c.v += 1
        return c.v
    return -1


def fresh(b: readonly[bytearray]) -> Own[bytearray]:
    # each result is a new bytearray: mutating it leaves `b` alone
    s = b.strip()
    s[0] = 65
    ls = b.lstrip()
    ls[0] = 66
    rs = b.rstrip()
    rs[0] = 67
    rc = b.rstrip(b" ")
    rc[0] = 68
    up = b.upper()
    up[0] = 69
    return s + ls + rs + rc + up


def main() -> None:
    print("loop:", Tally().total())
    print("overload:", via_overload({"a": 1}))
    sh = Shelf()
    print("optional:", peek(sh), bump(sh), peek(sh))
    b = bytearray(b" ab ")
    print("bytearray:", fresh(b), b)
