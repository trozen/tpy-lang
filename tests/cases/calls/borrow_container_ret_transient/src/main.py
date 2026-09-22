# A borrow-returning CONTAINER call result read TRANSIENTLY -- a truthiness
# test, a `while` head, a membership haystack -- renders the bare
# `std::vector<T>&` call: the position holds nothing past the full
# expression, so nothing binds off the reference.
# Every section mutates the source between two reads so the transient read is
# seen to go through the receiver's own storage, not a copy.
# Method and free-function spellings of the same call ride one verdict.
import asyncio
from typing import Iterator

from tpy import Own, int32


class B:
    items: list[int32]

    def __init__(self, t: int32) -> None:
        self.items = [t]

    def items_m(self) -> list[int32]:
        return self.items


def free_items(b: B) -> list[int32]:
    return b.items


def mk() -> Own[B]:
    return B(7)


# free function -- the truthiness test, method and free spellings.
def truthy(b: B) -> int32:
    n = 0
    if b.items_m():  # tpyc: ok
        n += 1
    if free_items(b):  # tpyc: ok
        n += 10
    return n


# free function -- the `while` head reads it once per iteration, so the
# emptied list must end the loop.
def while_head(b: B) -> int32:
    n = 0
    while b.items_m():  # tpyc: ok
        b.items.pop()
        n += 1
    return n


# free function -- the membership haystack, `in` and `not in`.
def membership(b: B) -> int32:
    n = 0
    if 1 in b.items_m():  # tpyc: ok
        n += 1
    if 9 not in free_items(b):  # tpyc: ok
        n += 10
    return n


# free function -- a TEMPORARY receiver: the prvalue lives to the end of the
# full expression, which is exactly where the transient read ends.
def temp_receiver() -> int32:
    if mk().items_m():  # tpyc: ok
        return 1
    return 0


class Reader:
    v: int32

    # constructor -- the same read inside a ctor body.
    def __init__(self, b: B) -> None:
        self.v = 0
        if b.items_m():  # tpyc: ok
            self.v = 1

    # method -- and inside a method body.
    def read(self, b: B) -> int32:
        if 1 in b.items_m():  # tpyc: ok
            return 1
        return 0


# generator -- inside a resumable frame's body.
def gen(b: B) -> Iterator[int32]:
    if b.items_m():  # tpyc: ok
        yield 1
    yield 0


async def coro(b: B) -> int32:
    # async -- the same inside a coroutine frame.
    if 1 in b.items_m():  # tpyc: ok
        return 1
    return 0


def main() -> None:
    b = B(1)
    print("truthy", truthy(b))
    print("membership", membership(b))
    # The source is read through, not snapshotted: emptying it flips the test.
    b.items.clear()
    print("truthyempty", truthy(b))
    print("membershipempty", membership(b))
    b6 = B(5)
    print("while", while_head(b6))
    print("temp", temp_receiver())
    b2 = B(2)
    r = Reader(b2)
    print("ctor", r.v)
    b3 = B(1)
    print("method", r.read(b3))
    b4 = B(3)
    for v in gen(b4):
        print("gen", v)
    b5 = B(1)
    print("async", asyncio.run(coro(b5)))


main()
