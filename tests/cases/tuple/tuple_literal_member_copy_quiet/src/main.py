# The inverse of tuple_literal_member_copy_sinks: a tuple literal member that
# does NOT copy where CPython aliases stays quiet -- a copy(), a fresh rvalue,
# an owned local at its last use (it moves), a borrow-form member (it aliases,
# and a write through it reaches the source, at a yield, a local, a walrus or
# a plain argument element), a value-only nested tuple.
from typing import Iterator

from tpy import Own, copy, int32


class C:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class H:
    q: tuple[int32, C]

    def __init__(self) -> None:
        self.q = (0, C(0))


# yield, explicit copy() into the Own element.
def yield_copy(c: C) -> Iterator[tuple[int32, Own[C]]]:
    yield (1, copy(c))  # tpyc: ok


# yield, fresh rvalue member.
def yield_fresh() -> Iterator[tuple[int32, Own[C]]]:
    yield (1, C(5))  # tpyc: ok


# yield, owned local at its last use moves into the element.
def yield_last_use() -> Iterator[tuple[int32, Own[C]]]:
    for i in range(2):
        c = C(i * 10)
        yield (i, c)  # tpyc: ok


# yield, a borrow element beside a fresh Own one: the borrow aliases.
def yield_mixed_borrow(b: C) -> Iterator[tuple[Own[C], C]]:
    yield (C(7), b)  # tpyc: ok


# yield, value-only nested tuple.
def yield_values() -> Iterator[tuple[int32, tuple[int32, str]]]:
    yield (1, (2, "two"))  # tpyc: ok


# argument: a fresh Own element and a borrowed plain element.
def take(t: tuple[Own[C], C]) -> int32:
    return t[0].v + t[1].v


def arg_quiet() -> None:
    b = C(2)
    print("arg_quiet", take((C(1), b)))  # tpyc: ok


def setitem_quiet(c: C) -> None:
    # subscript store: copy(), fresh member, last-use owned local.
    d: dict[int32, tuple[C | None, int32]] = {}
    d[0] = (copy(c), 1)  # tpyc: ok
    d[1] = (C(8), 2)  # tpyc: ok
    last = C(9)
    d[2] = (last, 3)  # tpyc: ok
    c.v = 99
    show("setitem_copy", d[0])
    show("setitem_fresh", d[1])
    show("setitem_last_use", d[2])


def show(tag: str, p: tuple[C | None, int32]) -> None:
    a, k = p
    if a is not None:
        print(tag, a.v, k)


def field_quiet(h: H) -> None:
    # field store: a fresh member and a last-use owned local move in.
    h.q = (1, C(11))  # tpyc: ok
    print("field_fresh", h.q[1].v)
    last = C(12)
    h.q = (2, last)  # tpyc: ok
    print("field_last_use", h.q[1].v)


def local_alias() -> None:
    # local: a direct reference member is borrow form, so the write reaches c.
    c = C(1)
    t = (1, c)  # tpyc: ok
    t[1].v = 21
    print("local_alias", c.v)


def walrus_alias() -> None:
    # walrus: the same borrow-form binding as the local.
    c = C(1)
    print("walrus_alias", (u := (9, c))[0])  # tpyc: ok
    c.v = 99
    print("walrus_alias", u[1].v)


def main() -> None:
    src = C(1)
    for p in yield_copy(src):
        p[1].v = 777
    print("yield_copy", src.v)
    for p in yield_fresh():
        print("yield_fresh", p[1].v)
    for p in yield_last_use():
        p[1].v = 777
        print("yield_last_use", p[1].v)
    b = C(2)
    for m in yield_mixed_borrow(b):
        m[1].v = 80
    print("yield_mixed_borrow", b.v)
    for vt in yield_values():
        print("yield_values", vt[1][1])
    arg_quiet()
    setitem_quiet(C(3))
    field_quiet(H())
    local_alias()
    walrus_alias()


main()
