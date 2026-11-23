# Members of a class at `Own[...]` that hand over what they hold: `Own[T]`,
# static and class methods, generator steps, a readonly field, `str`.
from __future__ import annotations
from typing import Callable, Iterator
from tpy import Own, int32, readonly
from fixtures import P


class Cell[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)

    def take(self, x: Own[T]) -> Own[T]:
        # `Own[T]` at `T = Own[P]` is `Own[P]`: the value moves through
        return x

    @staticmethod
    def make(v: T) -> Own[Cell[T]]:
        return Cell[T](v)

    @classmethod
    def build(cls, v: T) -> Own[Cell[T]]:
        return Cell[T](v)


class Bag[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def push(self, x: Own[T]) -> None:
        self.items.append(x)


class Gen[T]:
    f: Callable[[], T]

    def __init__(self, f: Callable[[], T]) -> None:
        self.f = f

    def gen(self) -> Iterator[T]:
        # each step is the factory's own result
        yield self.f()
        yield self.f()


class Ro[T]:
    v: readonly[T]

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)


class Tagger[T]:
    v: T

    def __init__(self, v: Own[T]) -> None:
        self.v = v

    # the method's own `U` is the call's, whatever the class's `T`
    def tag[U](self, u: U) -> U:
        return u


class Strs[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def get(self) -> T:
        return self.items[0]


def cell() -> None:
    c = Cell[Own[P]](P(1))  # tpyc: ok
    t = c.take(P(5))
    t.n = 6
    print("own-t", c.v.n, t.n)
    # the static and class methods' `T` is the class's: the value moves in
    s = Cell[Own[P]].make(P(2))  # tpyc: ok
    m = Cell[Own[P]].build(P(3))  # tpyc: ok
    s.v.n += 20
    m.v.n += 30
    print("static", s.v.n, m.v.n)


def own_param_list() -> None:
    # `Own[T]` at `T = Own[list[int32]]`: a list local moves at its last
    # use, a literal is built in place
    ob = Bag[Own[list[int32]]]()
    e = [8]
    ob.push(e)  # tpyc: ok
    ob.push([9])
    ob.items[0].append(1)
    print("own-list", len(ob.items), len(ob.items[0]))


def generator() -> None:
    # a generator step `yield self.f()` at `T = Own[P]` hands each fresh value
    g = Gen[Own[P]](lambda: P(7))  # tpyc: ok
    total = 0
    for z in g.gen():
        z.n += 1
        total += z.n
    print("gen", total)


def readonly_field() -> None:
    # a `readonly[T]` field at `Own[P]` reads as `readonly[P]`
    # (error_class_own_type_arg_readonly_field: a write through it is refused)
    r = Ro[Own[P]](P(3))  # tpyc: ok
    print("ro-field", r.v.n)


def method_u() -> None:
    t = Tagger[Own[P]](P(1))  # tpyc: ok
    x = P(2)
    # `U = P` lends: the result is `x` itself
    y = t.tag(x)
    y.n = 3
    print("method-u", x.n, y.n, t.v.n)


def strings() -> None:
    # `Own[str]`: a copy of a value type is no divergence and warns nothing
    s = Strs[Own[str]]()  # tpyc: ok
    s.items.append("s")
    print("str", s.get(), len(s.items))


def main() -> None:
    cell()
    own_param_list()
    generator()
    readonly_field()
    method_u()
    strings()
