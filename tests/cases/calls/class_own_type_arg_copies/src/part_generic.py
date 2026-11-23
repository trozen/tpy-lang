# A class at `Own[P]` through generic code that copies: a generic function
# over `Box[T]`, one spelled at `Own[P]`, an alias, two instantiations of
# one class, comprehension and closure call sites, a protocol parameter, a
# result type naming the class, and a spelled `T` a pending instance keeps
# (completed by a hint or by member calls in either order).
from __future__ import annotations
from typing import Protocol
from tpy import Array, Own, int32
from fixtures import Box, P


def first[T](b: Box[T]) -> T:
    return b.get()


def make_box[T](x: T) -> Own[Box[T]]:
    b = Box[T](x)
    b.push(x)
    return b


class Sink[T](Protocol):
    def push(self, x: T) -> None: ...


def feed_twice[T](s: Sink[T], x: T) -> int32:
    s.push(x)
    s.push(x)
    return 2


def make_arr[T]() -> Own[Array[Box[T], 1]]:
    return Array()


class Duo[A, B]:
    a: list[A]
    b: list[B]

    def __init__(self) -> None:
        self.a = []
        self.b = []

    def add_a(self, x: A) -> None:
        self.a.append(x)  # tpyc: warning(/may copy A into owned storage/)

    def add_b(self, y: B) -> None:
        self.b.append(y)  # tpyc: warning(/may copy B into owned storage/)

    def first(self) -> A:
        return self.a[0]


def duo_hint[T]() -> Own[Duo[T, int32]]:
    # the spelled `T` stays `T` when the return hint completes the instance
    d = Duo[T, _]()
    return d  # tpyc: ok


def duo_call[T]() -> Own[Duo[T, int32]]:
    # ... and when a member call completes it before the return settles it
    d = Duo[T, _]()
    d.add_b(3)
    return d  # tpyc: ok


def duo_early[T](x: T) -> Own[Duo[T, int32]]:
    # a member call at the spelled position before `_` is bound passes `x`
    # at the instance's `T`, as the twin `Duo[T, int32]()` does
    d = Duo[T, _]()
    d.add_a(x)  # tpyc: ok
    d.add_b(7)
    return d


# never consumed: BUGS.md#own-t-never-consumed-per-body
def duo_early_own[T](x: Own[T]) -> Own[Duo[T, int32]]:  # tpyc: warning(/'x' is never consumed/)
    # ... from an `Own[T]` parameter
    d = Duo[T, _]()
    d.add_a(x)  # tpyc: ok
    d.add_b(7)
    return d


def duo_late[T](x: T) -> Own[Duo[T, int32]]:
    # the neighbour order: `_` bound first
    d = Duo[T, _]()
    d.add_b(7)
    d.add_a(x)  # tpyc: ok
    return d


def duo_local[T](x: T) -> int32:
    # ... and an instance that never leaves the body
    d = Duo[T, _]()
    d.add_a(x)  # tpyc: ok
    d.add_b(3)
    return len(d.a) + d.b[0]


# an alias body forms the class where it is declared
type OwnBox = Box[Own[P]]  # tpyc: warning(/'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)


def through_generic() -> None:
    src = P(1)
    b = Box[Own[P]](src)  # tpyc: warning(/copies P into owned storage; use copy/) warning(/'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    src.n = 10
    # `first` infers `T = Own[P]` from the class argument; its parameter
    # names the class there
    f = first(b)  # tpyc: warning(/'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    f.n = 40
    print("first", b.v.n, f.n, src.n)
    # a generic body holding `Box[T]` instantiates the class at `Own[P]`
    m = make_box[Own[P]](P(3))  # tpyc: warning(/copies P into owned storage; use copy/) warning(/'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    m.items[0].n = 30
    print("make", m.v.n, m.items[0].n)


def alias() -> None:
    src = P(3)
    a: OwnBox = Box[Own[P]](src)  # tpyc: warning(/copies P into owned storage; use copy/) warning(/'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    src.n = 33
    g = a.get()
    g.n = 34
    print("alias", a.v.n, src.n, g.n)


def two_instantiations() -> None:
    # `Box[Own[list[int32]]]` beside `Box[Own[P]]`: each reports its copies
    a = Box[Own[list[int32]]]([1])  # tpyc: warning(/copies list\[int32\] into owned storage in 'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    ga = a.get()
    ga.append(2)
    print("two", len(a.v), len(ga))


def call_sites() -> None:
    b = Box[Own[P]](P(7))  # tpyc: warning(/'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    # a comprehension and a closure call the members; each result is a copy
    ns = [b.get().n + k for k in range(2)]

    def bump() -> int32:
        g = b.get()
        g.n += 1
        return g.n
    print("sites", ns[0], bump(), b.v.n)


def protocol_arg() -> None:
    b = Box[Own[P]](P(1))  # tpyc: warning(/'Box.get'/)
    q = P(2)
    # a protocol parameter at `Own[P]`: its `T` owns, the first push copies
    print("proto-feed", feed_twice[Own[P]](b, q))  # tpyc: warning(/copies P into owned storage; use copy/)
    b.items[0].n = 20
    q.n = 40
    print("proto-items", b.items[0].n, b.items[1].n, q.n)
    # at `P` the parameter lends, the box copies on its own
    c = Box[P](P(5))
    print("proto-feed-plain", feed_twice[P](c, q))  # tpyc: ok


def result_type() -> None:
    # the result type alone forms `Box[Own[P]]`
    a = make_arr[Own[P]]()  # tpyc: warning(/copies P into owned storage in 'Box.get' \(fixtures.py:25\)/)
    print("result-type", len(a))
    k = make_arr[int32]()  # tpyc: ok
    print("result-type-int", len(k))


def spelled_pending() -> None:
    # `Duo[Own[P], int32]` is formed: its getter copies
    d = duo_hint[Own[P]]()  # tpyc: warning(/copies P into owned storage in 'Duo.first'/)
    d.add_a(P(4))
    g = d.first()
    g.n = 40
    e = duo_call[Own[P]]()  # tpyc: warning(/'Duo.first'/)
    e.add_a(P(5))
    print("spelled-pending", d.a[0].n, g.n, e.a[0].n, e.b[0])
    # at `P` the getter lends
    f = duo_hint[P]()  # tpyc: ok
    f.add_a(P(6))
    f.first().n = 60
    print("spelled-pending-p", f.a[0].n)


def early_call() -> None:
    # at `P` the getter lends the element the early call stored
    d = duo_early(P(4))
    g = d.first()
    g.n = 11
    print("early-call", d.a[0].n, g.n, d.b[0])
    o = duo_early_own(P(3))
    o.a[0].n = 30
    print("early-call-own", o.a[0].n, o.b[0])
    late = duo_late(P(5))
    late.first().n = 50
    print("early-call-late", late.a[0].n, late.b[0])
    print("early-call-local", duo_local(P(6)))
    # at `int32`
    i = duo_early(8)
    j = duo_early_own(9)
    k = duo_late(10)
    print("early-call-int", i.a[0], j.a[0], k.a[0], duo_local(11))


def main() -> None:
    through_generic()
    alias()
    two_instantiations()
    call_sites()
    protocol_arg()
    result_type()
    spelled_pending()
    early_call()
