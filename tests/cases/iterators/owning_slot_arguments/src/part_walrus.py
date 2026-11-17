# A walrus argument at an owning element slot: `ys := xs` moves the owned
# local into `ys`, which the argument then reads. `ys` is read after the
# call, so the slot is lent `ys` and copies its elements, warned, at a free
# callee, a generator, a generator method and a coroutine alike.
import asyncio
from typing import Iterable, Iterator
from tpy import Own, int32


class R:
    def __init__(self, v: int32) -> None:
        self.v = v


def mk() -> Own[list[R]]:
    return [R(1), R(2)]


def take(xs: Iterable[Own[R]]) -> int32:
    t = 0
    for x in xs:
        x.v += 100
        t += x.v
    return t


def gen(xs: Iterable[Own[R]]) -> Iterator[int32]:
    for x in xs:
        x.v += 100
        yield x.v


class G:
    k: int32

    def __init__(self) -> None:
        self.k = 0

    def mgen(self, xs: Iterable[Own[R]]) -> Iterator[int32]:
        for x in xs:
            x.v += 100
            yield x.v + self.k


async def atake(xs: Iterable[Own[R]]) -> int32:
    t = 0
    for x in xs:
        x.v += 100
        t += x.v
    return t


def free_walrus() -> None:
    xs = mk()
    # `ys` stays readable: the callee mutates copies of its elements.
    t = take(ys := xs)  # tpyc: warning(/copies R elements/)
    print("walrus free", t, len(ys))


def gen_walrus() -> None:
    xs = mk()
    # `ys` stays readable: the frame owns copies of its elements.
    it = gen(ys := xs)  # tpyc: warning(/copies R elements/)
    print("walrus gen", list(it), len(ys))


def method_gen_walrus() -> None:
    xs = mk()
    g = G()
    # `ys` stays readable: the frame owns copies of its elements.
    it = g.mgen(ys := xs)  # tpyc: warning(/copies R elements/)
    print("walrus method gen", list(it), len(ys))


async def async_walrus() -> None:
    xs = mk()
    # `ys` stays readable: the frame owns copies of its elements.
    t = await atake(ys := xs)  # tpyc: warning(/copies R elements/)
    print("walrus async", t, len(ys))


def main() -> None:
    free_walrus()
    gen_walrus()
    method_gen_walrus()
    asyncio.run(async_walrus())
