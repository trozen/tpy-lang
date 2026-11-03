# A generic record at a `Ptr[...]` type argument: the field of type T holds
# the pointer, so a write through it reaches the original object.
from typing import Iterator
from tpy import Ptr, Own, readonly, int32
from tplib.box import Box
from objs import Ext


class A:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


class H[T]:
    p: T

    def __init__(self, p: T) -> None:
        self.p = p

    def get(self) -> T:
        return self.p


class S:
    # inverse: the spelled `Ptr[A]` field twin
    p: Ptr[A]

    def __init__(self, p: Ptr[A]) -> None:
        self.p = p


class Owner:
    h: H[Ptr[A]]

    def __init__(self, q: Ptr[A]) -> None:
        # field of type H[Ptr[A]] built in a constructor
        self.h = H(q)  # tpyc: ok


class User:
    def touch(self, q: Ptr[A]) -> None:
        # method body
        h = H(q)  # tpyc: ok
        h.p.v = 30


def gen(q: Ptr[A]) -> Iterator[int]:
    # generator body
    h = H(q)  # tpyc: ok
    yield h.p.v


def make(q: Ptr[A]) -> Own[H[Ptr[A]]]:
    # returned by value
    return H(q)  # tpyc: ok


def main() -> None:
    objs = [A(1), A(2)]
    q: Ptr[A] = objs[0]
    # inferred type argument
    h = H(q)  # tpyc: ok
    h.p.v = 7
    print("inferred", objs[0].v)
    # spelled type argument, write through the getter's result
    h2 = H[Ptr[A]](objs[1])  # tpyc: ok
    h2.get().v = 9
    print("spelled", objs[1].v)
    # readonly pointee
    r: Ptr[readonly[A]] = objs[0]
    h3 = H(r)  # tpyc: ok
    print("readonly", h3.p.v)
    # readonly scalar pointee
    n: Ptr[readonly[int32]] = None
    h4 = H(n)  # tpyc: ok
    print("readonly_scalar", h4.p is None)
    # cross-module pointee
    e = Ext(1)
    pe: Ptr[Ext] = e
    h6 = H(pe)  # tpyc: ok
    h6.p.v = 4
    print("cross_module", e.v)
    # Box over a Ptr, read through get()
    b = Box(q)  # tpyc: ok
    b.get().v = 11
    print("box", objs[0].v)
    own = Owner(objs[1])
    own.h.p.v = 12
    print("field", objs[1].v)
    User().touch(objs[0])
    print("method", objs[0].v)
    for x in gen(objs[1]):
        print("generator", x)
    m = make(objs[0])
    m.p.v = 13
    print("returned", objs[0].v)
    s = S(objs[1])
    s.p.v = 14
    print("spelled_field", objs[1].v)


main()
