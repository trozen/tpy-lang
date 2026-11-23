# The operand of an awaited call: a coroutine's own frame captures it (a
# reference type as the caller's object); a fresh argument whose render is a
# statement expression is held at its spelled type wherever a frame keeps
# it; a plain callee the statement awaits takes fresh arguments for the
# statement only.
import asyncio
from asyncio import Task
from typing import Iterable, Iterator, cast
from tpy import Own, char, int32
import part_plan_awaited_generic
import part_plan_awaited_selects
import part_plan_fresh_receivers
import part_plan_head_selects
import part_plan_reads_captured
import part_plan_slice_args


async def consume(it: Iterable[int32]) -> int32:
    t = 0
    for x in it:
        await asyncio.sleep(0)
        t += x
    return t


def count(it: Iterable[int32]) -> Iterator[int32]:
    for x in it:
        yield x


async def rep_awaited(k: int32) -> int32:
    # a list repeat an awaited coroutine keeps
    return await consume([k] * 3)  # tpyc: ok


def rep_gen(k: int32) -> Iterator[int32]:
    # ... a generator head keeps
    for x in count([k] * 2):  # tpyc: ok
        yield x


async def rep_task(k: int32) -> int32:
    # ... and a task's coroutine keeps
    t = asyncio.create_task(consume([k] * 3))  # tpyc: ok
    return await t


async def collect(cs: Iterable[char]) -> str:
    out = ""
    for c in cs:
        await asyncio.sleep(0)
        out += str(c)
    return out


def bump(n: int32) -> int32:
    return n + 1


async def uneval_args(n: int32) -> str:
    # a cast, a chained comparison in an f-string and in a ternary's
    # condition: each renders a statement expression the holder's type is
    # not spelled from
    a = await collect(cast(str, f"abc_{n}"))  # tpyc: ok
    b = await collect(f"cmp_{1 < bump(n) < 5}")  # tpyc: ok
    c = await collect("yes" if (1 < bump(n) < 5) else f"no_{n}")  # tpyc: ok
    return a + " " + b + " " + c


async def total(n: int32) -> None:
    await asyncio.sleep(0)
    print("awaited plain total", n)


def start(xs: list[int32], s: str) -> Own[Task[None]]:
    return asyncio.create_task(total(len(xs) + len(s)))


async def plain_awaited() -> None:
    k = 4
    # fresh arguments of a plain callee the statement awaits
    await start([k, k, k], f"abc_{k}")  # tpyc: ok


def main() -> None:
    print("awaited rep", asyncio.run(rep_awaited(2)))
    print("awaited rep gen", list(rep_gen(3)))
    print("awaited rep task", asyncio.run(rep_task(2)))
    asyncio.run(plain_awaited())
    print("awaited uneval", asyncio.run(uneval_args(2)),
          asyncio.run(uneval_args(7)))
    part_plan_awaited_selects.main()
    part_plan_awaited_generic.main()
    part_plan_head_selects.main()
    part_plan_reads_captured.main()
    part_plan_fresh_receivers.main()
    part_plan_slice_args.main()


main()
