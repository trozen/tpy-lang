# The field face at every position: method, constructor, generator, async, and
# a ValueType- and protocol-bounded `T`.
import asyncio
from typing import Iterator
from tpy import ValueType, int32
from fixtures import Box, Labeled, V


class Keeper[T]:
    def __init__(self, x: T, b: Box) -> None:
        # constructor: the field is set from the value, not the box's new n;
        # the hedged copy warning is for a reference `T`
        b.n = 30
        self.x = x  # tpyc: warning(/may copy T into field/)

    def method(self, x: T, b: Box) -> T:
        b.n = 31
        return x  # tpyc: ok


def gen[T](x: T, b: Box) -> Iterator[T]:
    b.n = 32
    # generator: the frame holds the value it was called with
    yield x


async def co[T](x: T, b: Box) -> T:
    b.n = 33
    await asyncio.sleep(0)
    return x


def bounded[T: ValueType](x: T, b: Box) -> T:
    b.n = 34
    return x


def proto[T: Labeled](x: T, b: Box) -> str:
    b.n = 35
    return x.label()


async def co_main(b: Box) -> None:
    b.n = 3
    print("async", await co(b.n, b), b.n)


def main() -> None:
    b = Box(1)
    k = Keeper(b.n, b)
    print("ctor", k.x, b.n)
    b.n = 2
    print("method", k.method(b.n, b), b.n)
    b.n = 4
    for v in gen(b.n, b):
        print("generator", v, b.n)
    asyncio.run(co_main(b))
    b.n = 5
    print("bounded int32", bounded(b.n, b), b.n)
    vb = V("vb")
    print("bounded V", bounded(vb, b).s)
    print("protocol V", proto(vb, b), b.n)
