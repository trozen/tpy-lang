# A generator object a frame holder keeps orders the frame structs: the
# held frame's struct is emitted first, whether the holder is an awaited
# cross-module callee's argument or a compound head's, and a held frame of
# another module's generic is spelled in that module.
import asyncio
from typing import Iterator
from tpy import int32
from part_frame_lib import Rec, consume_rec, gen_lib, total


async def awaited_cross_module() -> int32:
    # the generator object an awaited cross-module callee is handed
    return await total(nums(3))  # tpyc: ok


def head_in_generator() -> Iterator[int32]:
    # a head seat of a generator defined later in the module
    for v in doubled(nums(3)):  # tpyc: ok
        yield v


async def head_in_coroutine() -> int32:
    s = 0
    for v in doubled(nums(4)):  # tpyc: ok
        await asyncio.sleep(0)
        s += v
    return s


def bound_generic_in_loop() -> Iterator[int32]:
    for i in range(3):
        # a bound generator of another module's generic, held per pass
        g = gen_lib(Rec(i * 10))  # tpyc: ok
        yield consume_rec(g)


def doubled(it: Iterator[int32]) -> Iterator[int32]:
    for v in it:
        yield v * 2


def nums(n: int32) -> Iterator[int32]:
    for i in range(n):
        yield i


def main() -> None:
    print("order awaited", asyncio.run(awaited_cross_module()))
    print("order head gen", list(head_in_generator()))
    print("order head co", asyncio.run(head_in_coroutine()))
    print("order bound generic", list(bound_generic_in_loop()))
    g = gen_lib(Rec(5))
    print("order bound generic plain", consume_rec(g))
