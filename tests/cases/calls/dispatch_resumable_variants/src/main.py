# @dispatch sets of generators and coroutines: each variant is a resumable
# body with its own frame struct and factory, named by the variant's name.
# The readonly-twin paths (`twins_read`, `atwins_read`) are compiler-only:
# CPython's stand-in runs the first-declared variant
# (BUGS.md#stub-first-applicable-cannot-split-calls), so the twins there
# yield / return the same values.
import asyncio
from typing import Iterator, overload
from tpy import Own, auto_readonly, copy, dispatch, int32, readonly
from variants_mod import Feed, scaled, ticks


@dispatch
def gen(x: int32) -> Iterator[int32]:  # tpyc: frame_send(yes) frame_sync(yes)
    yield x
    yield x + 1


# its frame borrows the str argument (a view), so it is not Send
@dispatch
def gen(x: str) -> Iterator[int32]:  # tpyc: frame_send(no) frame_sync(yes)
    yield len(x)


@dispatch
def span(x: int32) -> Iterator[int32]:
    yield x


@dispatch
def span(x: int32, y: int32) -> Iterator[int32]:
    yield x
    yield y


@dispatch
def rep[T](obj: T) -> Iterator[T]:
    yield obj


@dispatch
def rep[T](obj: T, n: int32) -> Iterator[T]:
    i = 0
    while i < n:
        yield obj
        i += 1


class Counter:
    total: int32

    def __init__(self) -> None:
        self.total = 0

    @dispatch
    def feed(self, x: int32) -> Iterator[int32]:
        self.total += x
        yield self.total

    @dispatch
    def feed(self, x: str) -> Iterator[int32]:
        self.total += len(x)
        yield self.total

    @dispatch
    async def bump(self, x: int32) -> int32:
        self.total += x
        return self.total

    @dispatch
    async def bump(self, x: str) -> int32:
        self.total += len(x)
        return self.total


class Box[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = copy(v)

    @dispatch
    def out(self, x: int32) -> Iterator[T]:
        yield self.v

    @dispatch
    def out(self, x: str) -> Iterator[T]:
        yield self.v
        yield self.v


class Bag:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2]

    @auto_readonly
    def __iter__(self) -> Iterator[int32]:
        for x in self.xs:
            yield x

    @auto_readonly
    def items(self) -> Iterator[int32]:  # tpyc: frame_send(no) frame_sync(no)
        for x in self.xs:
            yield x


class Twins:
    v: int32
    calls: int32

    def __init__(self) -> None:
        self.v = 7
        self.calls = 0

    # explicit @readonly / mutable twins: two frames under one C++ name
    @dispatch
    def gen(self, x: int32) -> Iterator[int32]:  # tpyc: frame_send(no) frame_sync(no)
        self.calls += 1
        yield self.v + x

    @dispatch
    @readonly
    def gen(self, x: int32) -> Iterator[int32]:
        yield self.v + x

    # a twin pair beside a method spelled like the mutable twin's frame:
    # each gets a frame of its own
    @dispatch
    def vals(self) -> Iterator[int32]:
        # keeps this twin mutating (BUGS.md#readonly-twin-mutable-inferred-const)
        self.calls += 1
        yield 7

    @dispatch
    @readonly
    def vals(self) -> Iterator[int32]:
        yield 7

    def vals__mut(self) -> Iterator[int32]:  # tpyc: ok
        yield 8

    # explicit @readonly / mutable coroutine twins
    @dispatch
    async def step(self, x: int32) -> int32:
        # keeps this twin mutating (BUGS.md#readonly-twin-mutable-inferred-const)
        self.calls += 1
        return self.v + x

    @dispatch
    @readonly
    async def step(self, x: int32) -> int32:
        return self.v + x


class Plain:
    # no twin: a method named like a twin frame delegates to `values`
    def values(self) -> Iterator[int32]:
        yield 3

    def values__mut(self) -> Iterator[int32]:
        for x in self.values():  # tpyc: ok
            yield x + 1


class Base:
    @dispatch
    def gen(self, x: int32) -> Iterator[int32]:
        yield x

    @dispatch
    def gen(self, x: str) -> Iterator[int32]:
        yield len(x)


class Child(Base):
    pass


class Manager:
    log: list[str]

    def __init__(self) -> None:
        self.log = []

    # the zero-argument call `async with` makes picks the second variant
    @dispatch
    async def __aenter__(self, tag: str) -> "Manager":
        self.log.append("enter-tag")
        return self

    @dispatch
    async def __aenter__(self) -> "Manager":
        self.log.append("enter")
        return self

    async def __aexit__(self, kind: None, value: None, tb: None) -> bool:
        self.log.append("exit")
        return False


class GenManager[T]:
    log: list[str]

    def __init__(self) -> None:
        self.log = []

    @dispatch
    async def __aenter__(self, extra: T) -> int32:
        self.log.append("enter-extra")
        return 2

    @dispatch
    async def __aenter__(self) -> int32:
        self.log.append("enter")
        return 1

    async def __aexit__(self, kind: None, value: None, tb: None) -> bool:
        self.log.append("exit")
        return False


class Steps[T]:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __aiter__(self) -> "Steps[T]":
        return self

    # `async for` calls `__anext__()`: the second variant
    @dispatch
    async def __anext__(self, skip: T) -> int32:
        return 0

    @dispatch
    async def __anext__(self) -> int32:
        if self.n >= 2:
            raise StopAsyncIteration()
        self.n += 1
        return self.n


class Other:
    async def __anext__(self) -> int32:
        raise StopAsyncIteration()


class Source:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    # `async for` calls `__aiter__()`: the second variant
    @dispatch
    def __aiter__(self, x: int32) -> Own[Other]:
        return Other()

    @dispatch
    def __aiter__(self) -> "Source":
        return self

    async def __anext__(self) -> int32:
        if self.n >= 20:
            raise StopAsyncIteration()
        self.n += 10
        return self.n


class AutoExitManager:
    def __init__(self) -> None:
        pass

    async def __aenter__(self) -> int32:
        return 4

    # one written __aexit__ with its receiver clones is not a set
    @auto_readonly
    async def __aexit__(self, kind: None, value: None, tb: None) -> bool:  # tpyc: ok
        print("auto aexit exit")
        return False


class AutoSyncExit:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> int32:
        return 6

    @auto_readonly
    def __exit__(self, kind: None, value: None, tb: None) -> None:  # tpyc: ok
        print("auto exit exit")


class OneStubExit:
    def __init__(self) -> None:
        pass

    def __enter__(self) -> int32:
        return 3

    # one typing.overload stub and its implementation: one variant, not a set
    @overload
    def __exit__(self, kind: None, value: None, tb: None) -> None: ...

    def __exit__(self, kind: None, value: None, tb: None) -> None:  # tpyc: ok
        print("one stub exit exit")


class SyncManager:
    log: list[str]

    def __init__(self) -> None:
        self.log = []

    # `with` calls `__enter__()`: the second variant
    @dispatch
    def __enter__(self, x: int32) -> int32:
        self.log.append("enter-x")
        return 5

    @dispatch
    def __enter__(self) -> int32:
        self.log.append("enter")
        return 7

    def __exit__(self, kind: None, value: None, tb: None) -> None:
        self.log.append("exit")


@dispatch
async def co(x: int32) -> int32:  # tpyc: frame_send(yes) frame_sync(yes)
    return x + 1


@dispatch
async def co(x: str) -> int32:
    return len(x)


def chain() -> Iterator[int32]:
    # delegated: each `for` embeds the variant's own frame
    for v in gen(10):  # tpyc: ok
        yield v
    for w in gen("xyz"):
        yield w


def inherited(c: Child) -> Iterator[int32]:
    # delegated over a variant Child inherits: Base's frames
    for x in c.gen(3):  # tpyc: ok
        yield x
    for y in c.gen("abc"):
        yield y


def ro_items(b: Bag) -> Iterator[int32]:
    # delegated over @auto_readonly clones on a parameter inferred readonly
    for x in b.items():  # tpyc: ok
        yield x


def rw_items(b: Bag) -> Iterator[int32]:
    # the same delegation writing through the parameter
    for x in b.items():
        b.xs[1] += 100
        yield x


def sum_bag(b: Bag) -> int32:
    # a readonly receiver: the const clone's frame
    t = 0
    for x in b:  # tpyc: ok
        t += x
    return t


def twins_read(t: readonly[Twins]) -> None:
    # a readonly receiver: the @readonly twin's frame
    for x in t.gen(2):  # tpyc: ok
        print("twins ro", x)


async def atwins_read(t: readonly[Twins]) -> int32:
    # a readonly receiver: the @readonly coroutine twin's frame, through a
    # bound handle (an inline await there is
    # BUGS.md#await-readonly-receiver-coro-unowned)
    h = t.step(2)  # tpyc: ok
    return await h


async def amain() -> None:
    # free coroutine pair, awaited directly and through a bound handle
    a = await co(1)  # tpyc: ok
    h = co("abcd")  # tpyc: ok
    b = await h
    print("co", a, b)
    # method coroutine pair writing through self
    c = Counter()
    p = await c.bump(5)  # tpyc: ok
    q = await c.bump("xy")
    print("bump", p, q, c.total)
    # cross-module free coroutine set
    s1 = await scaled(2)  # tpyc: ok
    s2 = await scaled("ab")
    print("module co", s1, s2)
    # async with whose __aenter__ is a @dispatch set
    m = Manager()
    async with m:  # tpyc: ok
        m.log.append("body")
    print("aenter", m.log)
    # generic manager whose __aenter__ set has a variant taking T
    gm = GenManager[None]()
    async with gm as v:  # tpyc: ok
        print("generic aenter", v)
    print("generic aenter log", gm.log)
    # async for whose __anext__ is a @dispatch set on a generic iterator
    async for x in Steps[None]():  # tpyc: ok
        print("anext", x)
    # async for whose __aiter__ is a @dispatch set
    async for y in Source():  # tpyc: ok
        print("aiter", y)
    # explicit @readonly / mutable coroutine twins
    tw = Twins()
    r1 = await tw.step(1)  # tpyc: ok
    print("atwins", r1, tw.calls)
    print("atwins ro", await atwins_read(tw))
    # @auto_readonly __aexit__: one written exit, not a set
    am = AutoExitManager()
    async with am as z:  # tpyc: ok
        print("auto aexit", z)


def main() -> None:
    # free generator pair differing in a parameter type
    for v in gen(3):  # tpyc: ok
        print("free", v)
    for v in gen("abc"):
        print("free", v)
    # free generator pair differing in arity
    for v in span(4):
        print("arity", v)
    for v in span(5, 6):  # tpyc: ok
        print("arity", v)
    # generic free pair
    for s in rep("a"):
        print("generic", s)
    for n in rep(7, 2):  # tpyc: ok
        print("generic", n)
    # method generator pair writing through self
    c = Counter()
    for v in c.feed(2):
        print("method", v)
    for v in c.feed("abc"):  # tpyc: ok
        print("method", v)
    print("method total", c.total)
    # generic-class method generator pair
    bx = Box(9)
    for v in bx.out(1):
        print("box", v)
    for v in bx.out("s"):  # tpyc: ok
        print("box", v)
    # cross-module method generator set
    f = Feed()
    for v in f.pull(4):
        print("module", v)
    for v in f.pull("ab"):  # tpyc: ok
        print("module", v)
    print("module seen", f.seen)
    # cross-module free generator set
    for v in ticks(3):  # tpyc: ok
        print("module free", v)
    for v in ticks("abcd"):
        print("module free", v)
    # delegated for over a variant
    for v in chain():
        print("chain", v)
    # delegated for over an inherited variant
    for v in inherited(Child()):
        print("inherited", v)
    # @auto_readonly receiver clones: mutable and readonly frames
    bag = Bag()
    for x in bag:  # tpyc: ok
        # the frame borrows `bag`: the next step sees the write
        bag.xs[1] += 10
        print("clone", x)
    print("clone sum", sum_bag(bag))
    for x in rw_items(bag):
        print("clone rw", x)
    for x in ro_items(bag):
        print("clone ro", x)
    # explicit @readonly / mutable twins
    tw = Twins()
    for x in tw.gen(1):  # tpyc: ok
        print("twins", x, tw.calls)
    twins_read(tw)
    # a twin pair's mutable frame beside a method named like it
    for x in tw.vals():  # tpyc: ok
        print("twin frame", x)
    for x in tw.vals__mut():
        print("twin frame plain", x)
    # no twin pair: the delegation is not recursive
    for x in Plain().values__mut():  # tpyc: ok
        print("plain twin name", x)
    # @auto_readonly __exit__: one written exit, not a set
    ae = AutoSyncExit()
    with ae as e:  # tpyc: ok
        print("auto exit", e)
    # __exit__ as one typing.overload stub with its implementation
    with OneStubExit() as o:  # tpyc: ok
        print("one stub exit", o)
    # another variant's generator bound to a second name
    g = gen(1)
    for v in g:
        print("bound", v)
    g2 = gen("ab")  # tpyc: ok
    for v in g2:
        print("bound other", v)
    # sync with whose __enter__ is a @dispatch set
    sm = SyncManager()
    with sm as w:  # tpyc: ok
        print("enter", w)
    print("enter log", sm.log)
    asyncio.run(amain())


main()
