# Each variant of a method set is defined under its own name with a
# plain-name forwarder of its own signature: consuming and auto_own
# variants, a `Literal` set with and without a general variant, Own[T] and
# Fn variants, defaults, borrowed returns, and a variant name a
# property setter already takes (`set_foo__int32` -> `__v0`); inherited
# variants called at every receiver form, a subclass set whose generated name
# is an inherited one (`__v0`), a parameter and a type of the same spelling;
# the shapes a set may take (a generic class's `T`, two
# bases, receiver const-ness, a protocol-typed `typing.overload` stub).
# Where CPython's `dispatch` stub would run another variant (it runs the
# first declared one that applies, BUGS.md#stub-first-applicable-cannot-split-calls)
# the winner is declared first or the output does not depend on it; the
# pick is pinned by the snapshot.
from typing import Iterable, Literal, overload, Self
from tpy import auto_own, dispatch, Fn, int16, int32, Own, Ptr, readonly, StrView, ValueType


class Item:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Store:
    items: list[Item]
    log: list[str]

    def __init__(self) -> None:
        self.items = []
        self.log = []

    # Own[T] variants: the forwarder moves its argument on.
    @dispatch
    def add(self, it: Own[Item]) -> int32:
        self.items.append(it)
        return len(self.items)

    @dispatch
    def add(self, v: int32, times: int32 = 1) -> int32:
        for _ in range(times):
            self.items.append(Item(v))
        return len(self.items)

    # Borrowed returns: the variant and its forwarder return a reference.
    @dispatch
    def at(self, i: int32) -> Item:
        return self.items[i]

    @dispatch
    def at(self, i: str) -> Item:
        return self.items[len(i)]

    # An Fn variant (a template).
    @dispatch
    def apply(self, f: Fn[[int32], int32]) -> int32:
        return f(self.items[0].v)

    @dispatch
    def apply(self, x: str, k: int32) -> int32:
        return len(x) + k


    # A Literal set with a general variant, and one without.
    @dispatch
    def mode(self, m: Literal["r"]) -> str:
        return "read"

    @dispatch
    def mode(self, m: Literal["w"]) -> str:
        return "write"

    @dispatch
    def mode(self, m: str) -> str:
        return "other " + m

    @dispatch
    def flag(self, f: Literal[1]) -> str:
        return "one"

    @dispatch
    def flag(self, f: Literal[2]) -> str:
        return "two"

    # A variant name a property setter takes already.
    @property
    def foo__int32(self) -> int32:
        return len(self.items)

    @foo__int32.setter
    def foo__int32(self, v: int32) -> None:
        self.log.append(f"setter {v}")

    @dispatch
    def set_foo(self, v: int32) -> None:
        self.log.append(f"set_foo int32 {v}")

    @dispatch
    def set_foo(self, v: str) -> None:
        self.log.append(f"set_foo str {v}")


class Chain:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    # auto_own: a borrowing / consuming clone family per variant.
    @dispatch
    def bump(self: auto_own[Self], k: int32) -> auto_own[Self]:
        self.n += k
        return self

    @dispatch
    def bump(self: auto_own[Self], k: str) -> auto_own[Self]:
        self.n += len(k)
        return self

    # Consuming variants: the forwarder moves `*this` on.
    @dispatch
    def finish(self: Own[Self], k: int32) -> int32:
        return self.n + k

    @dispatch
    def finish(self: Own[Self], k: str) -> int32:
        return self.n + len(k)


class Base:
    @dispatch
    def m(self, x: int32) -> int32:
        return 1

    @dispatch
    def m(self, x: str) -> int32:
        return 2

    @dispatch
    def lit(self, x: Literal["a"]) -> int32:
        return 3

    @dispatch
    def lit(self, x: Literal["b"]) -> int32:
        return 4

    @dispatch
    def __add__(self, x: int32) -> int32:
        return 5

    @dispatch
    def __add__(self, x: str) -> int32:
        return 6

    @dispatch
    def pair(self, x: int32, y: str) -> int32:
        return 7

    @dispatch
    def pair(self, x: str, y: int32) -> int32:
        return 8

    @dispatch
    def set_q(self, x: int32) -> int32:
        return 9

    @dispatch
    def set_q(self, x: str) -> int32:
        return 10


class Child(Base):
    # A getter-only property declares no setter, so `set_q__int32` stays
    # Base's variant.
    @property
    def q__int32(self) -> int32:
        return 96

    # A set whose generated variant name is one of Base's (`pair__int32__str`)
    # takes a fresh one.
    @dispatch
    def pair__int32(self, x: str) -> int32:
        return 97

    @dispatch
    def pair__int32(self, x: bool) -> int32:
        return 98


class Grand(Child):
    def run(self) -> int32:
        # super() from a grandchild reaches Base's variant.
        return self.m(4) * 10 + super().m(5)


class Redef(Base):  # tpyc: warning(/hides .Base.m./)
    # A redefined set keeps its variants' names, hiding Base's.
    @dispatch
    def m(self, x: int32) -> int32:
        return 11

    @dispatch
    def m(self, x: str) -> int32:
        return 12


def via_param(c: Child) -> int32:
    return c.m(1)  # tpyc: ok


def via_own(c: Own[Child]) -> Own[Child]:
    print("inherited own:", c.m(1))  # tpyc: ok
    return c


def via_opt(c: Child | None) -> int32:
    if c is not None:
        return c.m(1)  # tpyc: ok
    return 0


def via_ptr(p: Ptr[Child]) -> int32:
    return p.m(1)  # tpyc: ok


class St:
    # A static set whose parameter is spelled like its variant: the
    # forwarder calls the variant through its class.
    @staticmethod
    @dispatch
    def m(m__int32: int32) -> int32:
        return m__int32

    @staticmethod
    @dispatch
    def m(x: str) -> int32:
        return len(x)


class m__str:
    v: int32

    def __init__(self) -> None:
        self.v = 7


class Named:
    # A parameter spelled like its variant, and a type spelled like the
    # other variant (the struct's member hides it).
    @dispatch
    def m(self, m__int32: int32) -> int32:
        return m__int32

    @dispatch
    def m(self, x: str) -> int32:
        return len(x)

    def use(self, x: m__str) -> int32:
        return x.v


class P(ValueType):
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    # A by-value parameter: the forwarder moves it on (no second copy).
    @dispatch
    def __lt__(self, other: "P") -> bool:
        return self.name < other.name

    @dispatch
    def __lt__(self, other: int32) -> bool:
        return False


class Box[T]:
    log: list[str]

    def __init__(self) -> None:
        self.log = []

    # A variant over the class's own type parameter beside a concrete one.
    @dispatch
    def put(self, x: T) -> str:
        self.log.append("T")
        return "T"

    @dispatch
    def put(self, x: int32) -> str:
        self.log.append("int32")
        return "int32"


class Mammal:
    def __init__(self) -> None:
        pass


class Pet:
    def __init__(self) -> None:
        pass


class Keeper:
    # Variants over two classes a third may derive from.
    @dispatch
    def m(self, x: Mammal) -> str:
        return "mammal"

    @dispatch
    def m(self, x: Pet) -> str:
        return "pet"


class Mix:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    # A mutating variant beside a readonly one.
    @dispatch
    def m(self, x: int32) -> str:
        self.n += x
        return "int32"

    @dispatch
    @readonly
    def m(self, x: int16) -> str:
        return "int16"

    # One C++ parameter type (`std::string_view`), receiver const-ness apart.
    @dispatch
    def s(self, x: StrView) -> str:
        self.n += 1
        return "StrView"

    @dispatch
    @readonly
    def s(self, x: str) -> str:
        return "str"


def ro(mx: readonly[Mix], a: str) -> str:
    return mx.s(a)


class Sum:
    # `typing.overload` stubs over a protocol-typed parameter.
    @overload
    def f(self, xs: Iterable[int32]) -> int32: ...

    @overload
    def f(self, xs: Iterable[int32], k: int32) -> int32: ...

    def f(self, xs: Iterable[int32], k: int32 = 0) -> int32:
        t = k
        for x in xs:
            t += x
        return t


def collisions() -> None:
    c = Child()
    print("inherited:", c.m(3), c.m("a"), c.lit("a"), c + 1)  # tpyc: ok
    print("inherited:", c.pair(1, "a"), c.pair__int32("a"), c.pair__int32(True))  # tpyc: ok
    print("getter-only:", c.q__int32, c.set_q(1), c.set_q("a"))  # tpyc: ok
    c2 = via_own(Child())
    print("inherited:", via_param(c), via_opt(c2), via_ptr(c), Grand().run())  # tpyc: ok
    r = Redef()
    print("redefined:", r.m(1), r.m("a"))  # tpyc: ok
    print("static:", St.m(3), St.m("ab"))  # tpyc: ok
    n = Named()
    print("named:", n.m(3), n.m("ab"), n.use(m__str()))  # tpyc: ok
    ps = [P("b"), P("a"), P("c")]
    print("value:", [p.name for p in sorted(ps)], P("a") < P("b"), P("a") < 3)  # tpyc: ok


def shapes() -> None:
    b: Box[int16] = Box()
    print("generic:", b.put(int16(1)), len(b.put(2)) > 0, len(b.log))  # tpyc: ok
    k = Keeper()
    print("bases:", k.m(Mammal()), k.m(Pet()))  # tpyc: ok
    mx = Mix()
    print("receiver:", mx.m(int16(1)), mx.m(5), mx.n)  # tpyc: ok
    v: StrView = "ab"
    print("receiver:", mx.s("a"), mx.s(v), mx.n)  # tpyc: ok
    # A readonly receiver runs the readonly variant (CPython's stub runs the
    # first declared one, so only the call is checked).
    print("receiver:", len(ro(mx, "x")) > 0)  # tpyc: ok
    sm = Sum()
    xs = [1, 2, 3]
    print("protocol:", sm.f(xs), sm.f(xs, 3))  # tpyc: ok


def borrow(s: Store) -> None:
    first = s.at(0)
    # The borrowed result aliases the stored item.
    first.v = 9
    print("borrow:", s.items[0].v, s.at("a").v)  # tpyc: ok


def main() -> None:
    s = Store()
    print("own:", s.add(Item(1)), s.add(2), s.add(3, 2))  # tpyc: ok
    borrow(s)
    print("fn:", s.apply(lambda x: x * 2), s.apply("abc", 1))  # tpyc: ok
    print("literal:", s.mode("r"), s.mode("w"), s.mode("x"), s.flag(1), s.flag(2))  # tpyc: ok
    s.set_foo(3)
    s.set_foo("a")
    s.foo__int32 = 5
    print("collision:", s.log, s.foo__int32)  # tpyc: ok
    c = Chain(1)
    c.bump(2).bump("ab")
    print("auto_own:", c.n, Chain(10).bump(1).n)  # tpyc: ok
    print("consuming:", Chain(5).finish(1), Chain(5).finish("xyz"))  # tpyc: ok
    collisions()
    shapes()


main()
