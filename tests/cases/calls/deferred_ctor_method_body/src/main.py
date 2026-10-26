# A generic construction whose type arguments a later use settles, in every
# method-body position: it defers as it does in a free function.
import asyncio
from typing import Iterator
from tpy import Array, Own, auto_readonly, copy, dispatch, int32
from bags import Bag
from bags import Bag as Sack
from bags import Cell
from bags import Keyed as K2
from bags import Mat as M
from tmod import TUser
from facade import Cell2


class Wipe[T]:
    items: list[T]

    def __init__(self, xs: list[int32]) -> None:
        xs.append(7)
        self.items = []

    def put(self, v: T) -> None:
        self.items.append(copy(v))

    # classmethod: `cls(xs)` bound to a local and completed by a later use
    @classmethod
    def local_cls(cls, xs: list[int32]) -> int32:
        w = cls(xs)  # tpyc: ok
        w.put(1)
        return len(w.items)

    # classmethod: `cls(xs)` completed by the ranked overload parameter
    @classmethod
    def ranked_cls(cls, xs: list[int32]) -> int32:
        return tw(cls(xs))  # tpyc: ok


class Sink:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Clear[T]:
    items: list[T]

    def __init__(self, xs: list[Sink]) -> None:
        xs.clear()
        self.items = []

    def put(self, v: T) -> None:
        self.items.append(copy(v))


@dispatch
def tw(w: Wipe[int32]) -> int32:
    return len(w.items) + 10


@dispatch
def tw(w: str) -> int32:
    return 0


class Self2[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def put(self, v: T) -> None:
        self.items.append(copy(v))

    # the class's own generic built in its own method, at another T
    def fresh(self) -> int32:
        o = Self2()  # tpyc: ok
        o.put("x")
        return len(o.items)


class Plain:
    n: int32
    b: Bag[int32]

    def __init__(self) -> None:
        self.n = 3
        # __init__ local
        loc = Bag()  # tpyc: ok
        loc.put(1)
        print("init", len(loc.items))
        # field store: the field's declared type gives T at once
        self.b = Bag()  # tpyc: ok
        self.b.put(2)

    # ordinary method
    def method(self) -> None:
        b = Bag()  # tpyc: ok
        b.put(1)
        print("method", b.items)

    # generic method: its own U settles the pending T
    def gen[U](self, u: U) -> None:
        b = Bag()  # tpyc: ok
        b.put(u)
        print("gen", len(b.items))

    @staticmethod
    def stat() -> None:
        b = Bag()  # tpyc: ok
        b.put("s")
        print("stat", b.items)

    # classmethod constructing another generic class by its name
    @classmethod
    def clsm(cls) -> None:
        b = Bag()  # tpyc: ok
        b.put(2.5)
        print("clsm", b.items)

    # nested def in a method
    def nested(self) -> None:
        def inner() -> None:
            b = Bag()  # tpyc: ok
            b.put(3)
            print("nested", b.items)
        inner()

    @auto_readonly
    def ar(self) -> int32:
        b = Bag()  # tpyc: ok
        b.put(self.n)
        return len(b.items)

    # generator method
    def genm(self) -> Iterator[int32]:
        b = Bag()  # tpyc: ok
        b.put(4)
        yield len(b.items)

    # async method
    async def co(self) -> int32:
        b = Bag()  # tpyc: ok
        b.put(5)
        return len(b.items)

    # comprehension in a method: each element's construction is completed by
    # the ranked overload parameter
    def comp(self, xs: list[int32]) -> None:
        rs = [tw(Wipe(xs)) for _ in range(2)]  # tpyc: ok
        print("comp", rs, xs)

    # @dispatch operand in a method
    def disp(self, xs: list[int32]) -> None:
        r = tw(Wipe(xs))  # tpyc: ok
        print("disp", r, xs)

    # an aliased class name, completed and returned owned
    def alias(self) -> Own[Bag[int32]]:
        s = Sack()  # tpyc: ok
        s.put(6)
        return s

    # a re-exported alias, its type arguments inferred at once: the value
    # has the declared class's type
    def reexported(self) -> Own[Cell[int32]]:
        c = Cell2(4)  # tpyc: ok
        return c

    # a type written through the alias beside a construction through it:
    # the parameter completes the construction
    def take(self, s: Sack[int32]) -> int32:
        return len(s.items)

    def alias_typed(self) -> None:
        s = Sack()  # tpyc: ok
        n = self.take(s)
        s.put(2)
        print("alias typed", n, s.items)

    # an aliased bounded generic, completed by a later use
    def bounded(self) -> None:
        k = K2()  # tpyc: ok
        k.put("x")
        print("bounded", len(k.keys))

    # an aliased generic with an integer parameter: the alias template
    # declares it as one
    def int_param(self) -> None:
        x = Array[int32, 2]()
        m = M(x)  # tpyc: ok
        print("int param", len(m.a))

    # the explicit twin's warning, on the construction line, although `e` is
    # not read after the call (BUGS.md#borrow-warning-not-last-use-aware)
    def borrow(self) -> None:
        xs = [Sink(1)]
        e = xs[0]
        n = e.n
        w = Clear(xs)  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter 'xs'/)
        w.put(1)
        print("borrow", n, len(xs))

    # try body
    def tried(self) -> None:
        try:
            b = Bag()  # tpyc: ok
            b.put(8)
            print("try", b.items)
        finally:
            print("try done")

    # the constructor mutates the caller's list, which is mutated again
    # between the construction and the completing put
    def mutated(self, xs: list[int32]) -> None:
        w = Wipe(xs)  # tpyc: ok
        xs.append(9)
        w.put(1)
        print("mutated inside", xs, len(w.items))

    # explicit twin of `method`: identical output
    def explicit(self) -> None:
        b = Bag[int32]()
        b.put(1)
        print("explicit", b.items)


def main() -> None:
    p = Plain()
    print("field", len(p.b.items))
    p.method()
    p.gen(7)
    Plain.stat()
    Plain.clsm()
    print("cls local", Wipe[int32].local_cls([1]))
    print("cls ranked", Wipe[int32].ranked_cls([2]))
    p.nested()
    print("ar", p.ar())
    for n in p.genm():
        print("genm", n)
    print("co", asyncio.run(p.co()))
    print("self2", Self2[int32]().fresh())
    p.comp([0])
    p.disp([1])
    a = p.alias()
    print("alias", a.items)
    c = p.reexported()
    print("reexported", c.v)
    p.alias_typed()
    p.bounded()
    p.borrow()
    p.int_param()
    # an import alias named like the class's own type parameter
    print("alias named T", TUser().count())
    p.tried()
    ys = [5]
    p.mutated(ys)
    # the caller sees both appends: the constructor's and the one after it
    print("mutated caller", ys)
    p.explicit()


main()
