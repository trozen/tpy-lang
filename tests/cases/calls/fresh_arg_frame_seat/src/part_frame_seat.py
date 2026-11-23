# A temporary a generator or coroutine keeps lives as long as it is needed:
# its statement, or the enclosing frame (docs/LANGUAGE_FEATURES.md "Tuples (Working)").
import asyncio
from typing import Iterator, Self
from asyncio import Task
from tpy import Own, Ptr, StrView, int32


def mks() -> str:
    return "a" * 60


def sp(s: str) -> StrView:
    return s


async def values(s: StrView) -> Own[list[int32]]:
    await asyncio.sleep(0)
    return [len(s), ord(s[0])]


async def awaited_head() -> int32:
    total = 0
    # an awaited call's kept argument crosses the `await`: a frame seat
    for x in await values(sp(mks())):  # tpyc: ok
        total += x
    return total


class Cnt:
    i: int32
    n: int32

    def __init__(self, n: int32) -> None:
        self.i = 0
        self.n = n

    def __iter__(self) -> Self:
        return self

    def __next__(self) -> int32:
        if self.i >= self.n:
            raise StopIteration
        self.i += 1
        return self.i


class Src:
    c: Cnt

    def __init__(self, n: int32) -> None:
        print("seat make", n)
        self.c = Cnt(n)


def cur(s: Src) -> Cnt:
    return s.c


def flips(f: bool, other: Cnt) -> Iterator[int32]:
    # the head is evaluated once, in the loop's setup: flipping `f` in the
    # body neither re-tests it nor builds `Src` again
    for x in (cur(Src(3)) if f else other):  # tpyc: ok
        f = not f
        yield x


class Box:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n, n + 1]


def walk(b: Box) -> Iterator[int32]:
    for x in b.xs:
        yield x


async def co(b: Box) -> int32:
    await asyncio.sleep(0)
    print("seat co", b.xs[0] + b.xs[1])
    return 0


def one(t: Own[Task[int32]]) -> Own[list[Task[int32]]]:
    out: list[Task[int32]] = []
    out.append(t)
    return out


class Keep:
    b: Ptr[Box]

    def __init__(self, b: Box) -> None:
        self.b = b

    def __enter__(self) -> Self:
        return self

    def __exit__(self, a, b, c) -> None:
        pass


async def handed_on() -> int32:
    # a task the head hands to the executor: its argument lives on the frame
    for t in one(asyncio.create_task(co(Box(5)))):  # tpyc: ok
        print("seat got")
    junk = [[k] * 50 for k in range(20)]
    await asyncio.sleep(0.01)
    total = len(junk)
    for i in range(3):
        # a consumer that borrows only for the call, once per pass
        total += sum(walk(Box(i)))  # tpyc: ok
        await asyncio.sleep(0)
    # a manager that keeps its argument across an `await`
    with Keep(Box(7)) as k:  # tpyc: ok
        await asyncio.sleep(0)
        total += k.b.xs[1]
    return total


class AMgr:
    b: Box

    def __init__(self, b: Own[Box]) -> None:
        self.b = b

    async def __aenter__(self) -> Box:
        await asyncio.sleep(0)
        return self.b

    async def __aexit__(self, exc_type: None, exc: None, tb: None) -> None:
        self.b.xs[0] += 100


async def async_with() -> None:
    # an `async with` target read after the statement
    async with AMgr(Box(1)) as t:  # tpyc: ok
        t.xs[0] += 1
    junk = [[k] * 30 for k in range(20)]
    t.xs[0] += 10
    print("seat async with", t.xs[0], len(junk))


def main() -> None:
    print("seat awaited", asyncio.run(awaited_head()))
    off = [v for v in flips(False, Cnt(4))]
    print("seat flips", off)
    on = [v for v in flips(True, Cnt(4))]
    print("seat flips", on)
    handed = asyncio.run(handed_on())
    print("seat handed_on", handed)
    asyncio.run(async_with())

