# `await task` -- type-erased await via Task[T]. The Task is constructed
# manually (no executor) via `tpy.coro.task_from_coro`.
from tpy import Int32
from asyncio import Task, task_from_coro
import asyncio

async def sub() -> Int32:
    return Int32(77)

async def caller() -> Int32:
    t = task_from_coro(sub())
    return await t

async def main_coro() -> None:
    result = await caller()
    print(result)

def main() -> None:
    asyncio.run(main_coro())

main()
