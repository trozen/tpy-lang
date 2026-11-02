# A caller passing its parameter to a structural-protocol parameter keeps it
# const when every protocol member its argument's type implements is read-only.
import asyncio
from typing import Iterable, Iterator, Protocol, Sequence, Sized
from tpy import char, int32, readonly, uint8


class Feed(Protocol):
    def feed(self, x: int32) -> int32: ...


class Peeker(Protocol):
    def peek(self) -> int32: ...
    def feed(self, x: int32) -> int32: ...


class Quiet:
    n: int32

    def __init__(self) -> None:
        self.n = 1

    def feed(self, x: int32) -> int32:
        return self.n + x


class Loud:
    n: int32

    def __init__(self) -> None:
        self.n = 1

    def feed(self, x: int32) -> int32:
        self.n += x
        return self.n


class Mixed:
    n: int32

    def __init__(self) -> None:
        self.n = 1

    def peek(self) -> int32:
        return self.n

    def feed(self, x: int32) -> int32:
        self.n += x
        return self.n


class Calm:
    n: int32

    def __init__(self) -> None:
        self.n = 4

    @readonly
    def feed(self, x: int32) -> int32:
        return self.n * x


class Adder[T](Protocol):
    def add(self, x: T) -> T: ...


class IntAdder:
    base: int32

    def __init__(self) -> None:
        self.base = 100

    def add(self, x: int32) -> int32:
        return self.base + x


class Countdown:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __iter__(self) -> "Countdown":
        return self

    def __next__(self) -> int32:
        if self.n == 0:
            raise StopIteration
        self.n -= 1
        return self.n


def total(it: Iterable[int32]) -> int32:
    t = 0
    for v in it:
        t += v
    return t


def use(p: Feed) -> int32:
    return p.feed(1)


def only_peeks(p: Peeker) -> int32:
    return p.peek()


def forward(it: Iterable[int32]) -> int32:
    return total(it)


def nested_use(p: Feed) -> int32:
    def go() -> int32:
        return p.feed(2)
    return go()


def feed_each(ps: Iterable[Loud]) -> None:
    for w in ps:
        w.feed(5)


def count_chars(it: Iterable[char]) -> int32:
    n = 0
    for _c in it:
        n += 1
    return n


def sum_u8(it: Iterable[uint8]) -> int32:
    t = 0
    for v in it:
        t += v
    return t


def size_of(s: Sized) -> int32:
    return len(s)


def first(s: Sequence[int32]) -> int32:
    return s[0]


def apply_add(a: Adder[int32]) -> int32:
    return a.add(1)


def rebound(p: Feed, o: Feed, flip: bool) -> int32:
    q = p
    if flip:
        q = o
    return q.feed(1)


def ping(p: Feed, n: int32) -> int32:
    if n == 0:
        return p.feed(1)
    return pong(p, n - 1)


def pong(p: Feed, n: int32) -> int32:
    return ping(p, n)


def alias_call(p: Feed) -> int32:
    q = p
    return q.feed(1)


def alias_reseat(p: Feed, again: bool) -> int32:
    q = p
    if again:
        q = p
    return q.feed(1)


def alias_iter(it: Iterable[int32]) -> int32:
    a = it
    t = 0
    for v in a:
        t += v
    return t


def alias_forward(it: Iterable[int32]) -> int32:
    a = it
    return total(a)


async def atotal(it: Iterable[int32]) -> int32:
    return total(it)


def gen_total(it: Iterable[int32]) -> Iterator[int32]:
    for v in it:
        yield v * 2


class Summer:
    s: int32

    def __init__(self, it: Iterable[int32]) -> None:
        self.s = total(it)

    def add(self, it: Iterable[int32]) -> int32:
        return self.s + total(it)


# free: list / dict / set / bytearray / bytes twins only read -> const callers
def free_list(xs: list[int32]) -> int32:
    return total(xs)  # tpyc: ok


def free_dict(d: dict[int32, str]) -> int32:
    return total(d)


def free_set(s: set[int32]) -> int32:
    return total(s)


def free_bytes(b: bytearray) -> int32:
    return sum_u8(b)


# value twins: a str or bytes parameter is a view, so there is no const to keep
def free_str(s: str) -> int32:
    return count_chars(s)


def free_immutable_bytes(b: bytes) -> int32:
    return sum_u8(b)


# a declared-@readonly member keeps the caller const
def k_calm(c: Calm) -> int32:
    return use(c)


# Sized / Sequence are read-only protocols: the caller was const before too
def k_sized(xs: list[int32]) -> int32:
    return size_of(xs) + first(xs)


# a generic user protocol whose member the twin only reads
def k_adder(a: IntAdder) -> int32:
    return apply_add(a)


# a whole alias of the protocol param is the param: read-only uses keep the
# caller const (only reads -- a write through the alias hits the filed copy,
# BUGS.md#protocol-param-local-alias-copies)
def k_alias_call(q: Quiet) -> int32:
    return alias_call(q)


def k_alias_iter(xs: list[int32]) -> int32:
    return alias_iter(xs) + alias_forward(xs)


# an alias re-seated to the same param on a branch still names that param, but
# the re-seat itself marks the param written, so the caller stays non-const
# (TODO 'Protocol slots into convert' 4b gaps)
def k_alias_reseat(q: Quiet) -> int32:
    return alias_reseat(q, True)


# a method passing `self` stays non-const where its concrete twin's method
# is const (BUGS.md#self-to-protocol-slot-stays-mutable)
class SelfFeeder:
    n: int32

    def __init__(self) -> None:
        self.n = 5

    def feed(self, x: int32) -> int32:
        return self.n + x

    def go(self) -> int32:
        return use(self)


# a protocol param cannot be rebound; a local re-seated between two of them
# aliases neither whole, so both are written directly and stay non-const
def k_rebound(q: Quiet, o: Quiet) -> int32:
    return rebound(q, o, True)


# mutual recursion forwarding the param: a writing twin stays non-const,
# a reading one keeps const
def k_ping_loud(w: Loud) -> int32:
    return ping(w, 2)


def k_ping_quiet(q: Quiet) -> int32:
    return ping(q, 2)


# user protocol: a read-only member keeps the caller const
def k_quiet(q: Quiet) -> int32:
    return use(q)


# user protocol: a writing member keeps the caller non-const
def k_loud(w: Loud) -> int32:
    return use(w)


# mixed protocol: one writing member keeps the caller non-const
def k_mixed(m: Mixed) -> int32:
    return only_peeks(m)


# self-iterator: its __next__ writes it
def k_countdown(c: Countdown) -> int32:
    return total(c)


# forwarding through a second protocol param
def k_forward(xs: list[int32]) -> int32:
    return forward(xs)


# use inside a nested def of the callee: a known gap (TODO 'Protocol slots
# into convert' 4b gaps), the replayed edge carries no twin, so the caller
# stays non-const
def k_nested(q: Quiet) -> int32:
    return nested_use(q)


# protocol member on a loop variable reached through p: an element write
def k_elems(ws: list[Loud]) -> None:
    feed_each(ws)


# async callee
def k_async(xs: list[int32]) -> int32:
    return asyncio.run(atotal(xs))


# generator-body and async-body callers
def g_caller(xs: list[int32]) -> Iterator[int32]:
    yield total(xs)


async def a_caller(xs: list[int32]) -> int32:
    return total(xs)


# generator callee
def k_gen(xs: list[int32]) -> int32:
    t = 0
    for v in gen_total(xs):
        t += v
    return t


# constructor and method callees
def k_ctor(xs: list[int32]) -> int32:
    s = Summer(xs)
    return s.add(xs)


class Holder:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2]

    # method caller passing its own field
    def sum_items(self) -> int32:
        return total(self.xs)


# comprehension and closure callers (the closure is the nested-def gap,
# TODO 'Protocol slots into convert' 4b gaps: it stays non-const)
def k_comp(xs: list[int32]) -> int32:
    ys = [total(xs) + i for i in range(2)]
    return ys[1]


def k_closure(xs: list[int32]) -> int32:
    def inner() -> int32:
        return total(xs)
    return inner()


def main() -> None:
    xs = [1, 2, 3]
    print("free_list", free_list(xs))
    print("free_dict", free_dict({1: "a", 2: "b"}))
    print("free_set", free_set({4, 5}))
    print("free_bytes", free_bytes(bytearray(b"\x01\x02")))
    print("free_str", free_str("abc"))
    print("free_immutable_bytes", free_immutable_bytes(b"\x03\x04"))
    print("calm", k_calm(Calm()))
    print("sized", k_sized(xs))
    print("adder", k_adder(IntAdder()))
    print("alias_call", k_alias_call(Quiet()))
    print("alias_iter", k_alias_iter(xs))
    print("alias_reseat", k_alias_reseat(Quiet()))
    print("self_method", SelfFeeder().go())
    print("rebound", k_rebound(Quiet(), Quiet()))
    pw = Loud()
    print("ping_loud", k_ping_loud(pw), pw.n)
    print("ping_quiet", k_ping_quiet(Quiet()))
    print("async", k_async(xs))
    for v in g_caller(xs):
        print("gen_body", v)
    print("async_body", asyncio.run(a_caller(xs)))
    q = Quiet()
    print("quiet", k_quiet(q), q.n)
    w = Loud()
    print("loud", k_loud(w), w.n)
    m = Mixed()
    print("mixed", k_mixed(m), m.n)
    c = Countdown(3)
    print("countdown", k_countdown(c), c.n)
    print("forward", k_forward(xs))
    print("nested", k_nested(q))
    ws = [Loud(), Loud()]
    k_elems(ws)
    print("elems", ws[0].n, ws[1].n)
    print("gen", k_gen(xs))
    print("ctor", k_ctor(xs))
    print("method", Holder().sum_items())
    print("comp", k_comp(xs))
    print("closure", k_closure(xs))


main()
# module level: no const to decide, the call behaves as in a function
print("module", total([7, 8]))
