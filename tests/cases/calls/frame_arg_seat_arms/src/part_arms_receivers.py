# A temporary that is the receiver of a field read or of a method call
# inside a generator's argument lives for the statement that consumes the
# generator -- in a plain function, a generator and a coroutine.
import asyncio
from typing import Iterator, Self
from tpy import int32


class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def me(self) -> Self:
        return self


class Box:
    s: Src

    def __init__(self, s: Src) -> None:
        # Box holds its own Src: the warned copy is the semantics here.
        self.s = s  # tpyc: warning(/copies Src into field/)


class Holder:
    s: Src

    def __init__(self, n: int32) -> None:
        self.s = Src(n)


def gen(s: Src) -> Iterator[int32]:
    yield s.n
    yield s.n + 1


def consume(g: Iterator[int32]) -> int32:
    total = 0
    junk = [Src(999) for _ in range(10)]
    for v in g:
        total += v
    return total + len(junk) - 10


def in_gen() -> Iterator[int32]:
    yield consume(gen(Holder(8).s))  # tpyc: ok


async def tick() -> int32:
    await asyncio.sleep(0)
    return 1


async def in_co() -> int32:
    return consume(gen(Holder(9).s)) + await tick()  # tpyc: ok


def main() -> None:
    # a field read of a fresh object, of an object built over a fresh one,
    # and a method returning its fresh receiver
    print("receivers field", consume(gen(Holder(5).s)))  # tpyc: ok
    print("receivers nested", consume(gen(Box(Src(3)).s)))  # tpyc: ok
    print("receivers method", consume(gen(Src(6).me())))  # tpyc: ok
    print("receivers gen", list(in_gen()))
    print("receivers co", asyncio.run(in_co()))
