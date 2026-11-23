# Error: more formations of a class at `Own[N]` refused by a member that
# copies, as the twin -- members a node calls without spelling them, a second
# static call, a result type, a protocol parameter, an owning local rebound
# in a loop, fields rotating or permuting their class's arguments.
from __future__ import annotations
from typing import Callable, Protocol
from tpy import Array, Own, Ptr, int32, nocopy, readonly


@nocopy
class N:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def peek[T](x: readonly[T]) -> bool:
    return True


class Jar[T]:
    v: T
    items: list[T]

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)
        self.items = []

    def put(self, x: T) -> None:
        self.items.append(x)  # tpyc: warning(/may copy T into owned storage/)

    def __setitem__(self, i: int32, x: T) -> None:
        self.put(self.v)

    def __enter__(self) -> T:
        return self.v

    def __exit__(self, et, ev, tb) -> None:
        pass

    def __bool__(self) -> bool:
        return peek[T](self.v)


class Tin[T]:
    v: T
    items: list[T]

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)
        self.items = []

    def put(self, x: T) -> None:
        self.items.append(x)  # tpyc: warning(/may copy T into owned storage/)

    def __delitem__(self, i: int32) -> None:
        self.put(self.v)

    def __len__(self) -> int32:
        return 1 if peek[T](self.v) else 0


class Lots[T]:
    @staticmethod
    def count(f: Callable[[], T]) -> int32:
        return 1

    @staticmethod
    def pick(f: Callable[[], T], xs: list[T]) -> T:
        return xs[0]


class Box[T]:
    v: T
    items: list[T]

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)
        self.items = []

    def get(self) -> T:
        return self.v

    def push(self, x: T) -> None:
        self.items.append(x)  # tpyc: warning(/may copy T into owned storage/)


class Sink[T](Protocol):
    def push(self, x: T) -> None: ...


def feed_twice[T](s: Sink[T], x: T) -> int32:
    s.push(x)
    s.push(x)
    return 2


def make_arr[T]() -> Own[Array[Box[T], 1]]:
    return Array()


class Inner[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def put(self, value: T) -> None:
        self.items.append(value)  # tpyc: warning(/may copy T into owned storage/)


class Outer[T]:
    v: T
    _in: Inner[T]

    def __init__(self, v: Own[T]) -> None:
        self.v = v
        self._in = Inner[T]()

    def put_loop(self, f: Callable[[], Own[T]]) -> None:
        loc = f()
        for _ in range(2):
            self._in.put(loc)
            loc = self.v


class Rot[A, B, C, D, E]:
    v: A
    kids: list[Rot[B, C, D, E, A]]

    def get(self) -> A:
        return self.v


class Rot4[A, B, C, D]:
    v: A
    kids: list[Rot4[B, C, D, A]]

    def get(self) -> A:
        return self.v


# two fields permuting six arguments: finitely many instantiations, but more
# than a nested walk could hold
class Perm[A, B, C, D, E, F]:
    v: A
    # a `Ptr` field: BUGS.md#finite-class-field-cycle-recursion-crash
    p: Ptr[int32]
    left: list[Perm[B, C, D, E, F, A]]
    right: list[Perm[B, A, C, D, E, F]]

    def get(self) -> A:
        return self.v


def mk() -> Own[N]:
    return N(7)


# item assignment, `with` and a truth test call members that copy
def dunders(f: Callable[[], Own[Jar[Own[N]]]]) -> None:  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage in 'Jar.__setitem__'/) error(/in 'Jar.__enter__'/) error(/in 'Jar.__bool__'/)
    j = f()
    j[0] = N(1)
    with j:
        print(1)
    if j:
        print(2)


# `del` and a truth test through `__len__` call members that copy
def len_dunders(f: Callable[[], Own[Tin[Own[N]]]]) -> None:  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage in 'Tin.__delitem__'/) error(/in 'Tin.__len__'/)
    t = f()
    del t[0]
    if t:
        print(3)


# a four-argument rotation reaches `Rot4[Own[N], ...]`
def rotated4(r: Rot4[int32, int32, int32, Own[N]]) -> int32:  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage in 'Rot4.get'/)
    return 1


# the permutations reach `Perm[Own[N], ...]`
def permuted(r: Perm[int32, int32, int32, int32, int32, Own[N]]) -> int32:  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage in 'Perm.get'/)
    return 1


# the field reaches `Rot[Own[N], ...]` five levels down
def rotated(r: Rot[int32, int32, int32, int32, Own[N]]) -> int32:  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage in 'Rot.get'/)
    return 1


def main() -> None:
    own_mk: Callable[[], Own[N]] = mk
    print(Lots.count(own_mk))  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage in 'Lots.pick'/)
    xs = [N(5)]
    print(Lots.pick(own_mk, xs).n)
    a = make_arr[Own[N]]()  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage in 'Box.get'/)
    print(len(a))
    b = Box[Own[N]](N(1))  # tpyc: error(/in 'Box.get'/)
    print(feed_twice[Own[N]](b, N(2)))  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage/)
    o = Outer[Own[N]](N(2))  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage in 'Outer.put_loop'/)
    o.put_loop(mk)
