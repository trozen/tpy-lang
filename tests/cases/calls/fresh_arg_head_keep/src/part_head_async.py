# Heads of an async function keep their fresh arguments across an await: a
# conditional operand, a fresh receiver, each `with` item before the next.
import asyncio
from typing import Self
from tpy import Own, int32


class Src:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        print("async make", n)
        self.xs = [n, n + 1]

    def items(self) -> list[int32]:
        return self.xs


class CM:
    s: Src

    def __init__(self, n: int32) -> None:
        self.s = Src(n)

    def __enter__(self) -> Self:
        print("async enter", self.s.xs[0])
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("async exit", self.s.xs[0])


def walk(s: Src) -> list[int32]:
    return s.xs


def pick(xs: list[int32]) -> list[int32]:
    return xs


def mk(n: int32) -> Own[Src]:
    return Src(n)


async def body(c: bool, other: list[int32]) -> int32:
    t = 0
    for x in pick(walk(Src(30)) if c else other):  # tpyc: ok
        await asyncio.sleep(0)
        t += x
    for x in mk(40).items():  # tpyc: ok
        await asyncio.sleep(0)
        t += x
    with CM(1) as a, CM(2) as b:  # tpyc: ok
        await asyncio.sleep(0)
        t += a.s.xs[0] + b.s.xs[0]
    return t


def main() -> None:
    on = asyncio.run(body(True, [9]))
    off = asyncio.run(body(False, [9]))
    print("async body", on, off)
