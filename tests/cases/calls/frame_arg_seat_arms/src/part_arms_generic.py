# An open `T` a generator or coroutine holds by reference at a reference
# type: its argument and what it borrows -- a nested call's, a field read's
# fresh receiver -- live for the statement, as its monomorphic twin's do,
# in a plain function, a generator and across an await; a value `T` is the
# frame's own copy.
import asyncio
from asyncio import Task
from typing import Iterator
from tpy import Own, int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Src:
    n: int32
    box: Box

    def __init__(self, n: int32) -> None:
        self.n = n
        self.box = Box(n)


def pick(s: Src) -> Src:
    return s


def gen_t[T](x: T) -> Iterator[T]:
    yield x


def genb(b: Box) -> Iterator[Box]:
    yield b


async def co_g[T](x: T) -> T:
    await asyncio.sleep(0)
    return x


def consume_b(g: Iterator[Box]) -> int32:
    total = 0
    junk = [Src(999) for _ in range(10)]
    for b in g:
        total += b.n
    return total + len(junk) - 10


def drain(g: Iterator[Src]) -> int32:
    total = 0
    junk = [Src(999) for _ in range(10)]
    for v in g:
        total += v.n
    return total + len(junk) - 10


def mk(i: int32) -> str:
    return "item-" + str(i)


async def tick() -> int32:
    await asyncio.sleep(0)
    return 1


def gen_generic() -> Iterator[int32]:
    yield drain(gen_t(pick(Src(6))))  # tpyc: ok


async def co_generic() -> int32:
    return drain(gen_t(pick(Src(5)))) + await tick()  # tpyc: ok


async def values() -> Own[list[str]]:
    ts: list[Task[str]] = []
    out: list[str] = []
    for i in range(3):
        # a value `T` is copied into the frame
        ts.append(asyncio.create_task(co_g(mk(i))))  # tpyc: ok
        for s in gen_gen_str(mk(i + 10)):  # tpyc: ok
            out.append(s)
    for t in ts:
        out.append(await t)
    return out


def gen_gen_str(x: str) -> Iterator[str]:
    yield x


def main() -> None:
    # the monomorphic twin and the generic arm over a field read of a
    # fresh receiver
    print("generic mono field", consume_b(genb(Src(5).box)))  # tpyc: ok
    print("generic field", consume_b(gen_t(Src(6).box)))  # tpyc: ok
    print("generic gen", list(gen_generic()))
    n = asyncio.run(co_generic())
    print("generic co", n)
    got = asyncio.run(values())
    print("generic values", got)
