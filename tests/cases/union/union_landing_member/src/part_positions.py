# Further boundaries: a read-modify-write subscript's getter and setter
# keys, a property setter, a fresh union argument at a `for` head (the
# generator-body twin is BUGS.md#frame-value-union-loop-var-rejects), a
# method's, a constructor's and an inherited constructor's parameter, a
# narrowed Optional's payload at a declaration, a reassignment and a return,
# and an isinstance-narrowed member of another union.
from typing import Iterator
from tpy import int32, int64, uint32, uint64


class M:
    v: int32

    def __init__(self) -> None:
        self.v = 0

    def __getitem__(self, key: int32 | str) -> int32:
        return self.v

    def __setitem__(self, key: int64 | str, value: int32) -> None:
        self.v = value


def key() -> int32:
    return 1


# subscript: the getter key lands in int32, the setter key in int64
def aug_subscript() -> None:
    m = M()
    m[key()] += 5  # tpyc: ok
    print("positions-aug", m.v)


class P:
    _v: int64 | uint64

    def __init__(self) -> None:
        self._v = uint64(0)

    @property
    def v(self) -> int64 | uint64:
        return self._v

    @v.setter
    def v(self, value: int64 | uint64) -> None:
        self._v = value


# property setter: the stored uint32 lands in int64
def property_setter(c: uint32) -> None:
    p = P()
    p.v = c  # tpyc: ok
    w = p.v
    print("positions-property", w, isinstance(w, uint64))


def pick(c: uint32) -> int64 | uint64:
    return c


def walk(u: int64 | uint64) -> Iterator[int64 | uint64]:
    yield u


# for head: a fresh union argument at the iterable's call
def for_head(c: uint32) -> None:
    for x in walk(pick(c)):  # tpyc: ok
        print("positions-for", x, isinstance(x, uint64))


class Q:
    def __init__(self, v: int32 | int64, tag: str) -> None:
        print(tag, isinstance(v, int64))

    def m(self, v: int32 | int64) -> None:
        print("positions-method", isinstance(v, int64))


class SubQ(Q):
    pass


# method, constructor and inherited constructor parameters
def params() -> None:
    q = Q(int64(5), "positions-ctor")  # tpyc: ok
    q.m(int64(6))  # tpyc: ok
    SubQ(int64(7), "positions-inherited-ctor")  # tpyc: ok


# narrowed Optional: the payload is an int32 value, landing in int64 as
# `w: int64 = x` would, not the Optional's storage copied whole
def narrowed_decl(x: int32 | None) -> None:
    if x is not None:
        w: int64 | str = x  # tpyc: ok
        print("positions-narrowed-decl", w, isinstance(w, str))


def narrowed_reassign(x: int32 | None) -> None:
    w: int64 | str = "none"
    if x is not None:
        w = x  # tpyc: ok
    print("positions-narrowed-reassign", w, isinstance(w, str))


def narrowed_return(x: int32 | None) -> int64 | str:
    if x is not None:
        return x  # tpyc: ok
    return "none"


# an isinstance-narrowed member of a DIFFERENT union lands as its scalar
def narrowed_member(x: str | int32) -> None:
    if not isinstance(x, str):
        w: int64 | str = x  # tpyc: ok
        print("positions-narrowed-member", w, isinstance(w, str))


class RA:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


# the same out of a union with a record (`not isinstance(x, RA)`: the
# CPython stubs answer no `isinstance(x, int32)`)
def narrowed_record_member(x: RA | int32) -> None:
    if not isinstance(x, RA):
        w: int64 | str = x  # tpyc: ok
        print("positions-narrowed-record-member", w, isinstance(w, str))


def main() -> None:
    narrowed_member(6)
    n = 7
    narrowed_record_member(n)
    narrowed_decl(3)
    narrowed_reassign(4)
    r = narrowed_return(5)
    print("positions-narrowed-return", r, isinstance(r, str))
    aug_subscript()
    property_setter(3)
    for_head(4)
    params()
