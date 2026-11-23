# Every argument temp reads the statement's keep plan: a temporary a frame
# keeps across a suspension is a frame holder whichever lowering arm
# builds it -- a lazy combinator's list, a generic `Iterable[T]` literal,
# a generator expression, a `*args` element -- and one consumed on the spot
# is no holder.
import asyncio
import itertools
from typing import Iterable, Iterator
from tpy import int32
import part_plan_reads_frames
import part_plan_reads_fresh
import part_plan_reads_generic
import part_plan_reads_holders
import part_plan_reads_kinds
import part_plan_reads_more
import part_plan_reads_owning
import part_plan_reads_restored


def mks(n: int32) -> str:
    return f"abcdefghijklmnopqrstuvwxyz0123456789-{n}"


def g_it[T](it: Iterable[T]) -> Iterator[T]:
    for x in it:
        yield x


def walk_cycle() -> Iterator[str]:
    # a list a lazy combinator iterates across the generator's yields
    for x in itertools.cycle([mks(10), mks(20)]):  # tpyc: ok
        yield x


async def generic_head() -> int32:
    # a literal at a generic generator's `Iterable[T]` in a coroutine head
    t = 0
    for y in g_it([10, 20]):  # tpyc: ok
        await asyncio.sleep(0)
        junk = [[999] * 20 for _ in range(5)]
        t += y + len(junk) - 5
    return t


def generic_gen_head() -> Iterator[int32]:
    # ... and in a generator head
    for y in g_it([3, 4]):  # tpyc: ok
        junk = [[999] * 20 for _ in range(5)]
        yield y + len(junk) - 5


def genexpr_head() -> Iterator[int32]:
    # a generator expression a generator's head keeps
    for x in g_it(i * 2 for i in range(4)):  # tpyc: ok
        yield x


async def total(xs: Iterable[int32]) -> int32:
    await asyncio.sleep(0)
    s = 0
    for x in xs:
        s += x
    return s


async def genexpr_awaited() -> int32:
    # ... and one an awaited coroutine keeps
    return await total(i * 2 for i in range(4))  # tpyc: ok


def main() -> None:
    out: list[str] = []
    for v in walk_cycle():
        out.append(v)
        if len(out) == 3:
            break
    print("reads cycle", out)
    print("reads generic co", asyncio.run(generic_head()))
    print("reads generic gen", list(generic_gen_head()))
    print("reads genexpr head", list(genexpr_head()))
    print("reads genexpr awaited", asyncio.run(genexpr_awaited()))
    part_plan_reads_more.main()
    part_plan_reads_fresh.main()
    part_plan_reads_frames.main()
    part_plan_reads_holders.main()
    part_plan_reads_kinds.main()
    part_plan_reads_restored.main()
    part_plan_reads_generic.main()
    part_plan_reads_owning.main()


main()
