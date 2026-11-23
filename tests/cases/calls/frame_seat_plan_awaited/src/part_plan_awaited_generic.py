# A fresh list at a protocol parameter with type arguments (`Iterable[T]`,
# `Iterable[int32]`) that a coroutine or generator keeps: the frame holder
# is initialized from the argument as its storage -- a list repeat in a
# generator head, an `or` / `and` of fresh lists awaited.
import asyncio
from typing import Iterable, Iterator
from tpy import int32


def each_t[T](it: Iterable[T]) -> Iterator[T]:
    for x in it:
        yield x


async def count_t[T](it: Iterable[T]) -> int32:
    await asyncio.sleep(0)
    n = 0
    for x in it:
        n += 1
    return n


async def sum_it(xs: Iterable[int32]) -> int32:
    await asyncio.sleep(0)
    s = 0
    for x in xs:
        s += x
    return s


def gen_repeat(k: int32) -> Iterator[int32]:
    # a list repeat at a generic `Iterable[T]` in a generator head
    for x in each_t([k, 1] * 2):  # tpyc: ok
        yield x


async def co_repeat(k: int32) -> int32:
    # ... and awaited
    return await count_t([k] * 3)  # tpyc: ok


async def co_or(k: int32) -> int32:
    # an `or` of fresh lists awaited at `Iterable[int32]`
    a = await sum_it([k, 2] or [3, 4])  # tpyc: ok
    # an `and` of fresh lists awaited at `Iterable[T]`
    b = await count_t([k] and [k, k, k])  # tpyc: ok
    return a * 10 + b


def main() -> None:
    print("generic repeat gen", list(gen_repeat(5)))
    print("generic repeat co", asyncio.run(co_repeat(2)))
    print("generic or co", asyncio.run(co_or(1)))
