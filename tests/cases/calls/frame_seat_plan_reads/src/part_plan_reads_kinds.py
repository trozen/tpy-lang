# A fresh argument in a frame head or an awaited operand, under each kind of
# node the keep plan walks into: a chained comparison, a walrus, `obj(args)`
# through `__call__`, a property setter, a class used as a callable, an
# augmented assignment, a comprehension's unpack clause (a `__getattr__` read
# there is refused, BUGS.md#awaited-ctor-arg-getattr-refused).
import asyncio
from typing import Callable, Iterator
from tpy import Own, int32


class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


async def co(s: Src) -> int32:
    await asyncio.sleep(0)
    return s.n


def gen(s: Src) -> Iterator[int32]:
    # the generator writes through what it keeps: the second value shows it
    yield s.n
    s.n += 100
    yield s.n


def pairs(s: Src) -> Iterator[tuple[int32, int32]]:
    # int pairs: a record element is BUGS.md#comp-unpack-pointer-tuple-skips-deref
    yield (1, s.n)
    s.n += 5
    yield (2, s.n)


async def chained() -> int32:
    # a fresh middle operand, compared across the suspension
    lo = 0
    if lo < Src(3).n < await co(Src(9)):  # tpyc: ok
        return 1
    return 0


def walrus() -> Iterator[int32]:
    # a walrus binding the argument a generator head keeps
    for v in gen((s := Src(4))):  # tpyc: ok
        yield v
    s.n += 1
    yield s.n


class Maker:
    def __call__(self, n: int32) -> Own[Src]:
        return Src(n)


def via_call() -> Iterator[int32]:
    # `obj(args)` lowered as `obj.__call__(args)`, its result kept
    m = Maker()
    for v in gen(m(5)):  # tpyc: ok
        yield v


class Slot:
    v: int32

    def __init__(self) -> None:
        self.v = 0

    @property
    def val(self) -> int32:
        return self.v

    @val.setter
    def val(self, x: int32) -> None:
        self.v = x


async def via_setter() -> int32:
    # a property setter whose value awaits over a fresh argument
    h = Slot()
    h.val = 1 + await co(Src(7))  # tpyc: ok
    return h.val


def gen_make(f: Callable[[int32], Own[Src]], n: int32) -> Iterator[int32]:
    s = f(n)
    yield s.n
    s.n += 1
    yield s.n


def class_callable() -> Iterator[int32]:
    # a class handed on as a callable value
    for v in gen_make(Src, 8):  # tpyc: ok
        yield v


async def aug() -> int32:
    # an augmented assignment's stored value awaits over a fresh argument
    total = 1
    total += await co(Src(9))  # tpyc: ok
    return total


async def drain(xs: list[int32]) -> int32:
    await asyncio.sleep(0)
    return sum(xs)


async def unpack_clause() -> int32:
    # a comprehension's unpack clause over a fresh argument, awaited
    return await drain([a * b for a, b in pairs(Src(10))])  # tpyc: ok


def main() -> None:
    print("kinds chained", asyncio.run(chained()))
    print("kinds walrus", list(walrus()))
    print("kinds call", list(via_call()))
    print("kinds setter", asyncio.run(via_setter()))
    print("kinds class", list(class_callable()))
    print("kinds aug", asyncio.run(aug()))
    print("kinds unpack", asyncio.run(unpack_clause()))
