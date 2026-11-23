# A generator local holding a method result that points at an ARGUMENT is
# held in the frame as the argument's alias: a write through it after a
# yield reaches the argument, as in CPython. The slot's const is the
# result's declared access, not the receiver's, since the result does not
# follow its receiver.
import asyncio
from typing import Iterator
from tpy import int32


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Bag:
    def __init__(self) -> None:
        pass

    def pick(self, o: Item) -> Item:
        return o

    def walk(self, a: Item) -> Iterator[int32]:
        # method generator: the alias of `a` survives the yield
        x = self.pick(a)  # tpyc: ok
        yield 1
        x.n = 9
        yield a.n


def gen(c: Bag, a: Item) -> Iterator[int32]:
    # free generator
    x = c.pick(a)  # tpyc: ok
    yield 1
    x.n = 5
    yield a.n


async def held(c: Bag, a: Item) -> int32:
    # async: the alias of `a` survives the await
    x = c.pick(a)  # tpyc: ok
    await asyncio.sleep(0)
    x.n = 7
    return a.n


def main() -> None:
    for v in gen(Bag(), Item(1)):
        print("free:", v)
    for v in Bag().walk(Item(2)):
        print("method:", v)
    print("async:", asyncio.run(held(Bag(), Item(3))))


main()
