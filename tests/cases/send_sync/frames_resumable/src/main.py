# FrameType classification for async coroutines and generators, pinned via
# tpyc: frame_send/frame_sync. Value params/locals give Send frames; borrowed
# params (list ref) and loop vars (may lower to raw-pointer slots) are
# conservatively non-Send; await chains AND in the sub-coroutine's frame;
# async methods capture the receiver by reference (never Send).
# A str/bytes param is stored owned in the frame but frame_traits classifies it
# by the borrow (StrView) traits -- conservatively non-Send (safe direction);
# gen_str below is frame_send(no) for that reason.
import asyncio
from typing import Iterator
from tpy import Int32, Own

async def inner(n: Int32) -> Int32:     # tpyc: frame_send(yes)
    return n

async def outer(n: Int32) -> Int32:     # tpyc: frame_send(yes)
    a = await inner(n)
    return a + 1

async def borrowing(xs: list[Int32]) -> Int32:  # tpyc: frame_send(no)
    return len(xs)

async def chained(n: Int32) -> Int32:   # tpyc: frame_send(no)
    xs = [n]
    return await borrowing(xs)

class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    async def bump(self) -> Int32:      # tpyc: frame_send(no)
        return self.n + 1

def gen_while(n: Int32) -> Iterator[Int32]:     # tpyc: frame_send(yes)
    i = 0
    while i < n:
        yield i
        i += 1

def gen_for(n: Int32) -> Iterator[Int32]:       # tpyc: frame_send(no)
    for i in range(n):
        yield i * 2

def gen_str(s: str) -> Iterator[Int32]:         # tpyc: frame_send(no) frame_sync(yes)
    yield len(s)

def gen_own(xs: Own[list[Int32]]) -> Iterator[Int32]:  # tpyc: frame_send(yes)
    yield len(xs)
    yield xs[0]

# A tuple with reference (record) elements lowers to a borrow-pointer frame
# field (std::tuple<Counter*, Counter*>), aliasing the caller -- non-Send
# even though both elements are Send. A value-element tuple is owned (Send).
async def tup_ref(pair: tuple[Counter, Counter]) -> Int32:  # tpyc: frame_send(no)
    return pair[0].n + pair[1].n

async def tup_val(pair: tuple[Int32, Int32]) -> Int32:      # tpyc: frame_send(yes)
    return pair[0] + pair[1]

def main() -> None:
    print(asyncio.run(outer(4)))
    xs = [1, 2, 3]
    print(asyncio.run(borrowing(xs)))
    print(asyncio.run(chained(7)))
    c = Counter(9)
    print(asyncio.run(c.bump()))
    print(list(gen_while(3)), list(gen_for(3)), list(gen_str("abc")))
    print(list(gen_own([4, 5])))
    print(asyncio.run(tup_ref((Counter(2), Counter(3)))))
    print(asyncio.run(tup_val((4, 5))))

main()
