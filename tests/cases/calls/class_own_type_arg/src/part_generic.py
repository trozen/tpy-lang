# A class at `Own[P]` through generic code that copies nothing: a generic
# function returning `Box[T]`, class inference, a nested argument, a static
# method binding its class's `T`, `Own[C[Own[T]]]` in a signature, and a
# bound's default (and a chain of them) taking the class argument's `T`.
from __future__ import annotations
from typing import Callable
from tpy import Fn, Own, int32
from fixtures import Box, Holder, P, mk


def wrap[T](f: Callable[[], T]) -> Own[Box[T]]:
    return Box[T](f())


def make_box_from[A, T](f: Fn[[A], T], k: A) -> Own[Box[T]]:
    return Box[T](f(k))


def mk_at(k: int32) -> Own[P]:
    return P(k)


class Later[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    @staticmethod
    def pick(f: Callable[[int32], Own[T]], xs: list[T]) -> T:
        return xs[0]


class Hold[T]:
    def __init__(self) -> None:
        pass

    def give(self, c: Own[Box[Own[T]]]) -> Own[Box[Own[T]]]:
        return c


class Shelf[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []


def promote[T, U: T](s: Shelf[U]) -> Own[Shelf[T]]:
    return Shelf[T]()


def promote_v[T, U: T](x: U) -> Own[Shelf[T]]:
    return Shelf[T]()


def both[T](a: Shelf[T], b: Shelf[T]) -> int32:
    return len(a.items) + len(b.items)


def bound_default[V](s: Shelf[V]) -> int32:
    # `T` defaults to `U`, which the class argument gave: `V` as written
    c = promote(s)
    return both(s, c)  # tpyc: ok


def bound_default_v[V](x: V) -> int32:
    # ... from a value: `V`'s storage form, as before
    c = promote_v(x)  # tpyc: ok
    return len(c.items)


def chain[T, U: T, V: U](s: Shelf[V]) -> Own[Shelf[T]]:
    return Shelf[T]()


def via_chain[X](s: Shelf[X]) -> int32:
    # a chain of bound defaults carries the class argument's `X` to `T`
    c = chain(s)
    return both(s, c)  # tpyc: ok


def through_generic() -> None:
    # an owning callable binds `T = Own[P]` in a generic function
    own_mk: Callable[[], Own[P]] = mk
    # Box's members copy at the `Own[P]` this call instantiates `wrap` at
    w = wrap(own_mk)  # tpyc: warning(/copies P into owned storage in 'Box.get'.*or declare 'f: Callable\[\[\], Own\[T\]\]'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    w.v.n = 5
    print("wrap", w.v.n)
    print("make_from", make_box_from(mk_at, 4).v.n)  # tpyc: warning(/'Box.get'.*or declare 'f: Fn\[\[A\], Own\[T\]\]'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)


def inference() -> None:
    own_mk: Callable[[], Own[P]] = mk
    # the callable's `Own[P]` result binds the class's `T = Own[P]`
    h = Holder(own_mk)
    g = h.get()
    g.n = 9
    # a fresh value binds `T = P`: the box lends
    b = Box(mk())
    lent = b.get()
    lent.n = 8
    print("infer", g.n, b.v.n)


def nested() -> None:
    # a nested argument: the inner box is the outer's `T`, which lends
    nb = Box[Box[Own[P]]](Box[Own[P]](P(6)))  # tpyc: warning(/'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    inner = nb.get()
    inner.v.n = 60
    print("nested", nb.v.v.n, inner.v.n)


def static_pick() -> None:
    # `f`'s `Own[P]` result binds the class's `T` to `P` through the
    # parameter's `Own[T]`: the result lends
    xs = [P(4)]
    r = Later.pick(mk_at, xs)  # tpyc: ok
    r.n = 40
    print("pick", xs[0].n)


def give() -> None:
    # `Own[Box[Own[T]]]` in a generic class's signature at `T = P`
    c = Box[Own[P]](P(5))  # tpyc: warning(/'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    d = Hold[P]().give(c)  # tpyc: warning(/'Box.get'/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    d.v.n = 50
    print("give", d.v.n)


def main() -> None:
    through_generic()
    inference()
    nested()
    static_pick()
    give()
    s = Shelf[Own[P]]()
    s.items.append(P(1))
    print("bound-default", bound_default(s), bound_default(Shelf[P]()),
          bound_default_v(P(2)))
    print("bound-chain", via_chain(s), via_chain(Shelf[P]()))
