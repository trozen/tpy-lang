# What a loop's call does with a view of an awaited frame's fresh argument
# decides how long the argument lives: printed, bound and sliced, counted
# by a function, formatted by a harmless `__str__` -- each keeps nothing.
import asyncio
from asyncio import Task
from typing import Iterator
from tpy import Own, Ptr, StrView, int32, readonly


class Src:
    text: str

    def __init__(self, n: int32) -> None:
        self.text = str(n) * 5


async def view(s: Src) -> StrView:
    await asyncio.sleep(0)
    return s.text


def count(acc: list[int32], s: StrView) -> None:
    acc.append(len(s))


class W:
    text: StrView

    def __init__(self, text: StrView) -> None:
        self.text = text

    def __str__(self) -> str:
        return "w:" + str(len(self.text))


async def wrap(s: Src) -> Own[W]:
    await asyncio.sleep(0)
    return W(s.text)


async def total(s: Src) -> int32:
    await asyncio.sleep(0)
    return len(s.text)


def tasks(s: Src) -> Iterator[Own[Task[int32]]]:
    yield asyncio.create_task(total(s))


async def co_n(n: int32) -> int32:
    await asyncio.sleep(0)
    return n


def tasks_n(s: Src) -> Iterator[Own[Task[int32]]]:
    yield asyncio.create_task(co_n(len(s.text)))


async def callees() -> None:
    acc: list[int32] = []
    for i in range(3):
        # print keeps none of its arguments
        print("callees print", await view(Src(i)))  # tpyc: ok
        v = await view(Src(i))  # tpyc: ok
        print("callees bound", v[1:3])
        # a function that keeps no view of its argument
        count(acc, await view(Src(i)))  # tpyc: ok
        # a record whose `__str__` keeps nothing
        print("callees format", await wrap(Src(i)))  # tpyc: ok
    print("callees count", acc)


async def loop_target() -> None:
    n = 0
    for i in range(3):
        # each task is awaited inside its own pass: the target escapes
        # nothing, so the generator's argument lives for its statement
        for t in tasks(Src(i)):  # tpyc: ok
            n += await t
    print("callees loop target", n)


class Keeps:
    p: Ptr[readonly[Src]]

    def __init__(self, s: readonly[Src]) -> None:
        self.p = s


async def unpack_target() -> None:
    for i in range(3):
        # a tuple target whose names stay inside the pass keeps nothing
        for k, w in [(0, Keeps(Src(i)))]:  # tpyc: ok
            print("callees unpack", k, w.p.text)
        await asyncio.sleep(0)


async def tasks_keep_nothing() -> None:
    ts: list[Task[int32]] = []
    for i in range(3):
        # the generator yields tasks that borrow nothing of its argument
        ts += [t for t in tasks_n(Src(i))]  # tpyc: ok
        for u in tasks_n(Src(i)):  # tpyc: ok
            ts.append(u)
        await asyncio.sleep(0)
    n = 0
    for tk in ts:
        n += await tk
    print("callees tasks keep nothing", n)


def main() -> None:
    asyncio.run(tasks_keep_nothing())
    asyncio.run(unpack_target())
    asyncio.run(callees())
    asyncio.run(loop_target())
