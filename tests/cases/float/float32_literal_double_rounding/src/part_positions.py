# Generator and async bodies: the constructor and a call argument take the
# same conversion there.
import asyncio
from typing import Iterator
from tpy import float32


def take(x: float32) -> float:
    return float(float32(x))


def gen() -> Iterator[float32]:
    a = float32(1.0000000596046448)  # tpyc: ok
    yield a
    yield float32(take(1.0000000596046448))  # tpyc: ok
    # the yielded value at the float32 element slot
    yield 1.0000000596046448  # tpyc: ok


async def coro() -> float:
    a = float32(1.0000000596046448)  # tpyc: ok
    await asyncio.sleep(0)
    return float(a) + take(1.0000000596046448)  # tpyc: ok


async def coro32() -> float32:
    await asyncio.sleep(0)
    # the returned value at the float32 result slot
    return 1.0000000596046448  # tpyc: ok


def main() -> None:
    print("generator", [float(float32(x)) for x in gen()])
    print("async", asyncio.run(coro()))
    print("async_return", float(float32(asyncio.run(coro32()))))
