# A generic body's class argument binds a callee's type parameter to the
# body's own `T` as written (`Crate[T]` against `Crate[U]` is `U = T`), at
# every inference site: a return hint, a call, a protocol argument (in
# either order, under renamed parameters), a pending instance, a spelled
# construction, an overloaded callee.
from __future__ import annotations
from typing import Callable, Protocol
from tpy import Own, dispatch, int32
from fixtures import N, P, mk


class Crate[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def put(self, x: T) -> None:
        self.items.append(x)  # tpyc: warning(/may copy T into owned storage/)

    def crate(self, k: int32) -> Crate[T]:
        return self


class HasCrate[A](Protocol):
    def crate(self, k: int32) -> Crate[A]: ...


class HasPair[A, B](Protocol):
    def crate(self, k: B) -> Crate[A]: ...


def fresh[T]() -> Own[Crate[T]]:
    # inferred from the return hint: `Crate[T]` itself
    c = Crate()  # tpyc: type(Crate[T])
    return c


def sink[T](c: Crate[T], y: T) -> None:
    c.put(y)


# the warning is true at `T = P` only (BUGS.md#own-t-never-consumed-per-body)
def hand[T](c: Crate[T], x: Own[T]) -> None:  # tpyc: warning(/never consumed/)
    # `T` from the class argument: the `T` slot owns at `Own[P]`
    sink(c, x)  # tpyc: ok


def one[A](h: HasCrate[A]) -> int32:
    return 1


def pair[A, B](x: A, h: HasPair[A, B]) -> int32:
    return 1


def via_proto[T](h: Crate[T]) -> int32:
    # the protocol's argument binds from the class argument
    return one(h)  # tpyc: ok


def via_pair[T](x: T, h: Crate[T]) -> int32:
    # a value and a class argument bind one parameter, in either order
    return pair(x, h)  # tpyc: ok


def pair_rev[X, Y](h: HasPair[X, Y], x: X) -> int32:
    return 2


def via_pair_rev[T](x: T, h: Crate[T]) -> int32:
    # the class argument first, under the callee's own parameter names
    return pair_rev(h, x)  # tpyc: ok


def pending_same_t[T](src: Crate[T], x: Own[T]) -> int32:
    # a pending instance bound to `T` by a `Crate[T]` parameter
    def pin(k: Crate[T]) -> None:
        pass
    c = Crate()
    pin(c)
    c.items.append(x)
    c.put(c.items[0])  # tpyc: ok
    return len(c.items)


class Two[K, V]:
    ks: list[K]
    vs: list[V]

    def __init__(self) -> None:
        self.ks = []
        self.vs = []

    def adopt(self, src: Crate[K]) -> None:
        pass

    def put_v(self, v: V) -> None:
        self.vs.append(v)  # tpyc: warning(/may copy V into owned storage/)


def size[K, V](t: Two[K, V]) -> int32:
    return len(t.vs)


def two_class_first[T](src: Crate[T]) -> int32:
    # a pending instance bound over two statements: the class argument first
    t = Two()
    t.adopt(src)
    t.put_v(1)
    return size(t)  # tpyc: ok


def two_value_first[T](src: Crate[T]) -> int32:
    # ... and the value first
    t = Two()
    t.put_v(1)
    t.adopt(src)
    return size(t)  # tpyc: ok


class Lid[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)


def take_lid[T](c: Lid[T]) -> int32:
    return 1


# never consumed: BUGS.md#own-t-never-consumed-per-body, as at `hand`
def lid_arg[T](v: Own[T]) -> int32:  # tpyc: warning(/never consumed/)
    # the spelled construction binds `take_lid`'s `T` to `T` itself
    return take_lid(Lid[T](v))  # tpyc: ok


class Maker[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def make(self, f: Callable[[], T]) -> T:
        return f()


def make_via[T](f: Callable[[], T]) -> T:
    # a pending instance pinned to `T`: at `Own[N]` its member hands the
    # callable's result over, nothing is copied
    def pin(k: Maker[T]) -> None:
        pass
    m = Maker()
    pin(m)
    return m.make(f)  # tpyc: ok


def mk_n() -> Own[N]:
    return N(11)


@dispatch
def tally[U](c: Crate[U], y: U) -> int32:
    c.put(y)
    return len(c.items)


@dispatch
def tally[U](c: Crate[U], y: U, k: int32) -> int32:
    c.put(y)
    return len(c.items) + k


# never consumed: BUGS.md#own-t-never-consumed-per-body, as at `hand`
def via_overload[T](c: Crate[T], x: Own[T], y: Own[T]) -> int32:  # tpyc: warning(/'x' is never consumed/) warning(/'y' is never consumed/)
    # each variant the ranking tries binds `U` from the class argument
    return tally(c, x) + tally(c, y, 10)  # tpyc: ok


def main() -> None:
    e: Crate[int32] = fresh()
    o: Crate[Own[P]] = fresh()  # tpyc: ok
    print("binder-fresh", len(e.items), len(o.items))
    c = Crate[Own[P]]()
    hand(c, P(3))
    c.items[0].n = 30
    print("binder-hand", c.items[0].n)
    # at `P` the same callee, a fresh value
    d = Crate[P]()
    sink(d, P(4))
    d.items[0].n = 40
    print("binder-sink", d.items[0].n)
    q = P(5)
    print("binder-proto", via_proto(d), via_pair(q, d))
    k = Crate[int32]()
    k.put(1)
    print("binder-int", via_proto(k), via_pair(2, k))
    # at `Own[P]`: the value's parameter is the class's `T`, which owns
    print("binder-own-pair", via_pair(P(6), c), via_pair_rev(P(7), c))
    print("binder-int-pair", via_pair_rev(3, k))
    # at `Own[P]` the element handed to `put` is an owned copy, as the twin's
    print("binder-pending", pending_same_t(c, P(8)))  # tpyc: warning(/copies P into owned storage/)
    print("binder-pending-p", pending_same_t(d, P(9)))  # tpyc: ok
    print("binder-two", two_class_first(c), two_value_first(c),
          two_class_first(k), two_value_first(k))
    print("binder-lid", lid_arg(P(12)), lid_arg(13))
    own_n: Callable[[], Own[N]] = mk_n
    own_p: Callable[[], Own[P]] = mk
    print("binder-maker", make_via(own_n).n, make_via(own_p).n)
    oc = Crate[Own[P]]()
    print("binder-overload", via_overload(oc, P(14), P(15)))
    oc.items[0].n = 140
    print("binder-overload-own", oc.items[0].n, len(oc.items))
