# Type-parameter bounds of protocols are honored, and a generic's type
# arguments satisfy its declared bounds wherever a type is spelled.
import asyncio
from typing import Iterator, Protocol, overload
from tpy import Equatable, Own, ValueType, dynamic, int32
import bnd_cyc_q
import bnd_cyc_p


class Sized[T: ValueType](Protocol):
    def size(self) -> int32: ...

    def item(self) -> T: ...


class Box[T: ValueType]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v


class Bag:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def size(self) -> int32:
        return self.n

    def count(self) -> int32:
        return self.n * 10

    def item(self) -> int32:
        return self.n + 1


# positions: a bounded protocol at a param and an Optional param; a bounded
# record at a return, a field, a local and a global
def at_param(p: Sized[int32]) -> int32:  # tpyc: ok
    return p.size()


def at_optional(p: Sized[int32] | None) -> int32:  # tpyc: ok
    return 0


def at_return(n: int32) -> Own[Box[int32]]:  # tpyc: ok
    return Box(n)


class Holder:
    b: Box[int32]  # tpyc: ok

    def __init__(self, n: int32) -> None:
        self.b = Box(n)


g_box: Box[int32] = Box(8)  # tpyc: ok


def positions() -> None:
    b = Bag(2)
    loc: Box[int32] = Box(3)  # tpyc: ok
    h = Holder(4)
    print("positions", at_param(b), at_optional(None), at_return(1).v, h.b.v, loc.v, g_box.v)
    b.n = 7  # the protocol argument aliases b
    print("positions aliased", at_param(b))


# forwarding: a bounded U passes its bound on to P[U] and Box[U] -- in a
# function, a record field, a record base, a protocol parent, a generic method
def forward[U: ValueType](p: Sized[U], b: Box[U]) -> int32:  # tpyc: ok
    return p.size()


class Keeps[U: ValueType]:
    b: Box[U]  # tpyc: ok

    def __init__(self, b: Own[Box[U]]) -> None:
        self.b = b

    def measure[V: ValueType](self, p: Sized[V]) -> int32:  # tpyc: ok
        return p.size()


class SubBox[U: ValueType](Box[U]):  # tpyc: ok
    pass


class Measured[U: ValueType](Sized[U], Protocol):  # tpyc: ok
    def count(self) -> int32: ...


# a bound naming an earlier sibling parameter, which passes its own bound on
class Paired[U: ValueType, V: Sized[U]](Protocol):  # tpyc: ok
    def pair(self) -> int32: ...


def sibling[U: ValueType, V: Sized[U]](v: V) -> int32:  # tpyc: ok
    return v.size()


# a method's bound naming its class's bounded parameter
class Outer[T: ValueType]:
    def use[U: Sized[T]](self, x: U) -> int32:  # tpyc: ok
        return x.size()


# a type-parameter bound (`U: T`) passes on the bound it names
def chain[T: ValueType, U: T](x: T, b: Box[U]) -> int32:  # tpyc: ok
    return 7


# an overload implementation passing its bounded T into Box[T]
@overload
def first[T: ValueType](b: Box[T]) -> int32: ...
@overload
def first(b: str) -> int32: ...
def first[T: ValueType](b: Box[T] | str) -> int32:  # tpyc: ok
    if isinstance(b, str):
        return len(b)
    return 1


def via_parent(m: Measured[int32]) -> int32:
    return m.size() + m.count()


def forwarding() -> None:
    b = Bag(3)
    k = Keeps(Box(1))
    print("forwarding", forward(b, Box(2)), k.b.v,
          k.measure(b), SubBox(6).v, via_parent(b), sibling(b))
    print("forwarding scopes", Outer[int32]().use(b), chain(1, Box(2)),
          first(Box(1)), first("abc"))


# an F-bounded protocol: P[C] requires C to satisfy Comparable[C], an
# inherited method included
class Comparable[T](Protocol):
    def compare(self, other: T) -> int32: ...


class Ranked[T: Comparable[T]](Protocol):  # tpyc: ok
    def rank(self) -> int32: ...


class Score:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def compare(self, other: 'Score') -> int32:
        return self.v - other.v

    def rank(self) -> int32:
        return self.v


class SubScore(Score):
    pass


def top(r: Ranked[Score]) -> int32:  # tpyc: ok
    return r.rank()


def sub_top(r: Ranked[SubScore]) -> int32:  # tpyc: ok
    return r.rank()


def f_bounded() -> None:
    s = Score(4)
    print("f-bounded", top(s), sub_top(SubScore(9)))


# @dynamic: a bounded T is a value type, so `-> T` and `-> Own[T]` are one
# slot, through a plain bounded protocol and through a redeclaring child
@dynamic
class Src[T: ValueType](Protocol):
    def get(self) -> Own[T]: ...


@dynamic
class Sink[T: ValueType](Protocol):
    def get(self) -> T: ...

    def put(self, v: T) -> None: ...


class Mid[U: ValueType](Src[U], Sink[U], Protocol):  # tpyc: ok
    def tag(self) -> int32: ...


class MidRev[U: ValueType](Sink[U], Src[U], Protocol):  # tpyc: ok
    def tag(self) -> int32: ...


@dynamic
class Top(Mid[int32], Protocol):
    def extra(self) -> int32: ...


class Counter(Top):
    n: int32

    def __init__(self) -> None:
        self.n = 3

    def get(self) -> Own[int32]:
        return self.n

    def put(self, v: int32) -> None:
        self.n += v

    def tag(self) -> int32:
        return 100

    def extra(self) -> int32:
        return 1000


def via_top(t: Top) -> int32:
    t.put(4)
    return t.get()


@dynamic
class Peek[T: ValueType](Protocol):
    def f(self) -> T: ...


@dynamic
class OwnPeek[U: ValueType](Peek[U], Protocol):  # tpyc: ok
    def f(self) -> Own[U]: ...


class Ticker:
    n: int32

    def __init__(self) -> None:
        self.n = 6

    def f(self) -> int32:
        self.n += 1
        return self.n


def peek(a: Peek[int32]) -> int32:
    return a.f()


def dynamic_slots() -> None:
    c = Counter()
    r = via_top(c)
    print("dynamic", r, c.extra(), c.n)  # the write through Top reached c
    t = Ticker()
    print("dynamic redeclared", peek(t), t.n)


# @dynamic bounded protocol with an Optional[T] parameter: the value form
@dynamic
class Taker[T: ValueType](Protocol):
    def take(self, x: T | None) -> int32: ...


class TakeImpl:
    def take(self, x: int32 | None) -> int32:
        if x is None:
            return 5
        return x


def take_none(p: Taker[int32]) -> int32:
    return p.take(None)


def optional_t() -> None:
    print("optional", take_none(TakeImpl()))


# a bounded generic printing a narrowed `T | None` field: a free function
# and a method parameter of the record's own type
class Slot[T: ValueType](ValueType):
    x: T | None

    def __init__(self, x: T | None) -> None:
        self.x = x

    def show_other(self, s: 'Slot[T]') -> None:
        if s.x is not None:
            print("slot method", s.x)  # the narrowed field prints


def show_slot[T: ValueType](s: Slot[T]) -> None:
    if s.x is not None:
        print("slot function", s.x)  # the narrowed field prints


def slots() -> None:
    s = Slot(1)
    show_slot(s)
    s.show_other(Slot(2))


# a same-module forward reference: a record bounded by a protocol declared
# after it
class Early[T: Late]:  # tpyc: ok
    t: T

    def __init__(self, t: Own[T]) -> None:
        self.t = t


class Late(Protocol):
    def size(self) -> int32: ...


def forward_ref() -> None:
    e = Early(Bag(11))
    print("forward ref", e.t.size())


# scope positions: a generator local, a nested def's signature, an async param
def gen() -> Iterator[int32]:
    b: Box[int32] = Box(21)  # tpyc: ok
    yield b.v


def bounded_gen[T: ValueType](x: T) -> Iterator[T]:  # tpyc: ok
    yield x
    yield x


class Gen[T: ValueType]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v

    def each(self, x: T) -> Iterator[T]:  # tpyc: ok
        yield x


async def bounded_async[T: ValueType](x: T) -> T:  # tpyc: ok
    return x


def nested() -> int32:
    def inner(b: Box[int32]) -> int32:  # tpyc: ok
        return b.v
    return inner(Box(22))


async def boxed_async(b: Box[int32]) -> int32:  # tpyc: ok
    return b.v


# a method re-declaring its class's bounded T names the class's T (a
# per-method bound), so it keeps the class's bound and adds its own
class EqBox[T: Equatable]:
    v: T

    def __init__(self, v: Own[T]) -> None:
        self.v = v


class Shadow[T: ValueType]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v

    def take[T](self, b: Box[T]) -> T:  # tpyc: ok
        return b.v

    def same[T: Equatable](self, b: Box[T], e: EqBox[T]) -> bool:  # tpyc: ok
        return self.v == b.v and b.v == e.v


# an import cycle: a bounded protocol extends its peer's bounded protocol
def cycle(c: bnd_cyc_q.Counted[int32]) -> int32:
    return c.size() + c.count() + bnd_cyc_p.peer_tag()


def main() -> None:
    positions()
    forwarding()
    f_bounded()
    dynamic_slots()
    optional_t()
    slots()
    forward_ref()
    for v in gen():
        print("scopes generator", v)
    print("scopes nested", nested())
    print("scopes async", asyncio.run(boxed_async(Box(23))))
    for g in bounded_gen("ab" * 2):
        print("bounded generator", g)
    for m in Gen(1).each(3):
        print("bounded generator method", m)
    print("bounded async", asyncio.run(bounded_async(4)))
    print("cycle", cycle(Bag(1)))
    sh = Shadow(int32(5))
    print("shadow", sh.take(Box(int32(6))), sh.same(Box(int32(5)), EqBox(int32(5))))


main()
