# A fresh argument a coroutine / generator frame keeps lives as in an
# ordinary function: its statement, or the frame slot the keep plan gives it.
import asyncio
from typing import Iterator

from tpy import Ptr, StrView, int32, nocopy, readonly

import part_borrowed_element
import part_fresh_element
import part_fresh_with
import part_generator
import part_receiver
import part_receiver_forms
import part_receiver_fresh
import part_root_seat
import part_slice


def mk(i: int32) -> str:
    return "abcdefghij"[i:i + 1] * 30


async def show(s: StrView) -> str:
    await asyncio.sleep(0)
    return str(s[0:3])


def views(s: StrView) -> Iterator[StrView]:
    yield s


@nocopy
class Src:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n, n + 1]


class Reg:
    p: Ptr[readonly[Src]]

    def __init__(self, keep: readonly[Src]) -> None:
        self.p = keep


async def bad(s: readonly[Src], reg: Reg) -> int32:
    await asyncio.sleep(0)
    reg.p = s
    return s.xs[0]


def ident(s: Src) -> Src:
    return s


def head(s: StrView) -> StrView:
    return s[1:]


async def total(s: Src) -> int32:
    await asyncio.sleep(0)
    return s.xs[0] + len(s.xs)


async def loop_slot() -> None:
    # Loop head in a coroutine: the task over the frame-held `mk(i)` is
    # awaited in the same pass, before the next pass reassigns the slot.
    out: list[str] = []
    for i in range(3):
        for v in views(mk(i)):  # tpyc: ok
            t = asyncio.create_task(show(v))
            out.append(await t)
    print("loop_slot", out)


async def foreign(reg: Reg) -> int32:
    # A callee storing a pointer to the frame-held `Src`: right only because
    # the read happens while the slot still holds the last pass's value
    # (BUGS.md#frame-handle-outlives-fresh-argument).
    t = 0
    for i in range(3):
        t += await bad(Src(i * 10 + 1), reg)  # tpyc: ok
    return t * 1000 + reg.p.xs[0]


async def nested_temp() -> None:
    # A task over a borrow of a nested temporary, awaited in the scope that
    # created it.
    t = asyncio.create_task(total(ident(Src(7))))  # tpyc: ok
    print("nested_temp", await t)
    # The str face: a task over a view a call returns of a fresh string.
    u = asyncio.create_task(show(head(mk(1))))  # tpyc: ok
    print("nested_str", await u)


# A module-level receiver: the global is handed as the object it holds.
g_summer = part_receiver.Summer(20)


async def global_recv() -> None:
    print("receiver_global", await g_summer.add(3), g_summer.base)  # tpyc: ok


def main() -> None:
    asyncio.run(loop_slot())
    keep = Src(0)
    print("foreign", asyncio.run(foreign(Reg(keep))))
    asyncio.run(nested_temp())
    part_receiver.main()
    part_receiver_forms.main()
    part_receiver_fresh.main()
    part_root_seat.main()
    asyncio.run(global_recv())
    part_slice.main()
    part_generator.main()
    part_borrowed_element.main()
    part_fresh_element.main()
    part_fresh_with.main()


main()
