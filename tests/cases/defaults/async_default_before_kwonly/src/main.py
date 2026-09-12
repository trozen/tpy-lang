# An async/generator FACTORY signature is emitted by its own params emitter, so
# it needs the same default-suppression gate the plain-function one has: a
# default before a required keyword-only param has no C++ spelling.
import asyncio
from typing import Iterator

from tpy import int64


async def scaled(a: int64, b: int64 = 10, *, c: int64) -> int64:
    return a * 100 + b * 10 + c


def counted(n: int64 = 2, *, step: int64) -> Iterator[int64]:
    # A generator factory shares the async factory's params emitter, so it
    # needs the same gate.
    for i in range(n):
        yield i * step


async def drive() -> None:
    print(await scaled(1, c=3))
    print(await scaled(1, 2, c=3))


def main() -> None:
    asyncio.run(drive())
    for v in counted(step=5):
        print(v)


main()
