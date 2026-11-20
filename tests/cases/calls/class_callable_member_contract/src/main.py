# A generic class's Callable[..., T] field and method signature read as a
# function's Callable: a bare T result is a borrow at a reference
# instantiation (the caller sees the object the callable lends), Own[T] a
# fresh value. Each section mutates after the boundary to show the aliasing.
from typing import Callable, Iterator
from tpy import Own, int32
from fixtures import G, H, P, fresh_list, ident, lend, lend_h, seven
import part_members


class Lazy[T]:
    f: Callable[[], T]

    def __init__(self, f: Callable[[], T]) -> None:
        self.f = f

    def make(self) -> T:
        return self.f()  # tpyc: ok

    @staticmethod
    def call_now(f: Callable[[], T]) -> T:
        return f()  # tpyc: ok


class DefaultMap[K, V]:
    factory: Callable[[], Own[V]]
    data: dict[K, V]

    def __init__(self, factory: Callable[[], Own[V]]) -> None:
        self.factory = factory
        self.data = {}

    def get(self, k: K) -> V:
        if k not in self.data:
            self.data[k] = self.factory()  # tpyc: ok
        return self.data[k]


class Map1[T]:
    g: Callable[[T], T]

    def __init__(self, g: Callable[[T], T]) -> None:
        self.g = g

    def apply(self, x: T) -> T:
        return self.g(x)  # tpyc: ok


class Gen[T]:
    f: Callable[[], T]

    def __init__(self, f: Callable[[], T]) -> None:
        self.f = f

    def run(self) -> Iterator[T]:
        yield self.f()  # tpyc: ok


def main() -> None:
    # free function: a lending callable at Lazy[P] hands back G itself
    r = Lazy[P](lend).make()  # tpyc: ok
    r.n = 51
    print("lend", G.n)
    # value instantiation
    print("value", Lazy[int32](seven).make() + 1)
    # Callable[[], Own[V]] member: a fresh list per key, mutations reach the map
    d = DefaultMap[str, list[int32]](fresh_list)
    d.get("a").append(1)
    d.get("a").append(2)
    d.get("b").append(3)
    print("factory", len(d.data["a"]), len(d.data["b"]), d.get("a") is d.get("b"))
    # Callable[[T], T] at P lends its argument through
    q = P(1)
    m = Map1[P](ident).apply(q)
    m.n = 2
    print("lend-through", q.n, m is q)
    # generator: a yield of the member's borrow lends G
    for x in Gen[P](lend).run():  # tpyc: ok
        x.n = 52
    print("gen", G.n)
    # from outside the class: the member type is the instantiation's
    b = Lazy[int32](seven)
    print("outside-value", b.f() + 1)  # tpyc: ok
    lp = Lazy[P](lend)
    o = lp.f()  # tpyc: ok
    o.n = 53
    print("outside-ref", G.n, o is G)
    # field store from outside: the member takes another lending callable
    lp.f = lend_h  # tpyc: ok
    h = lp.make()
    h.n = 61
    print("store", H.n, h is H)
    # static method: its Callable[[], T] parameter at the class's T
    t = Lazy[P].call_now(lend)
    t.n = 54
    print("static", G.n, t is G)
    part_members.main()


main()
