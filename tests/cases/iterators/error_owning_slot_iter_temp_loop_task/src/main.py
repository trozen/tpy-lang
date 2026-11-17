# An iterator object over a fresh list, passed to an `async def` owning
# element slot inside a loop whose tasks outlive the iteration, is refused
# rather than lent from a temporary the task outlives (docs/LANGUAGE_FEATURES.md
# "A frame never receives a temporary"): the iterator is passed as itself, so
# the call gets the shared-slot error its generator twin gets.
import asyncio
from asyncio import Task
from typing import Iterable
from tpy import Own, int32


class R:
    def __init__(self, v: int32) -> None:
        self.v = v


def mk(i: int32) -> Own[list[R]]:
    return [R(i), R(i * 10)]


async def atake(xs: Iterable[Own[R]]) -> int32:
    t = 0
    for x in xs:
        t += x.v
    return t


async def amain() -> None:
    tasks: list[Task[int32]] = []
    for i in range(1, 4):
        # Every task would hold an iterator the loop pass destroys.
        tasks.append(asyncio.create_task(atake(iter(mk(i)))))  # tpyc: error(/one shared slot of the enclosing frame/)
    for t in tasks:
        print(await t)


def main() -> None:
    asyncio.run(amain())


main()
