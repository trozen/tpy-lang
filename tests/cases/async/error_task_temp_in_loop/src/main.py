# A temporary argument of a factory call is seated on ONE field of the
# enclosing frame per call SITE, which serves a loop only while at most one
# handle built there is alive. A Task created in the loop outlives the
# iteration, so every task would borrow the last iteration's list -- the seat
# is refused instead of silently aliasing.
import asyncio

from asyncio import Task
from tpy import Int32


async def add_step(step: Int32, s: list[Int32]) -> Int32:
    s[0] += step
    await asyncio.sleep(0.0)
    s[1] += step
    return s[0] + s[1]


async def gather_loop() -> Int32:
    tasks: list[Task[Int32]] = []
    for i in range(2):
        # `[1, 2]` is the temporary and the Task is the handle that outlives
        # the iteration; the same line outside a loop stays admitted.
        tasks.append(asyncio.create_task(add_step(i, [1, 2])))  # tpyc: error(/one shared slot of the enclosing frame/)
    total = 0
    for t in tasks:
        total += await t
    return total


def main() -> None:
    print("res:", asyncio.run(gather_loop()))


main()
