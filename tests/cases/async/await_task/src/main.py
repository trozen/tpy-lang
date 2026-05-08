# `await task` -- type-erased await via Task[T]. The Task is constructed
# via the runtime's Task<T>::from_coro factory, exposed here as a
# cpp_template helper since asyncio.create_task lands later.
from tpy.extern import cpp_template
from tpy import Int32
from tpy.coro import Task
import asyncio

async def sub() -> Int32:
    return Int32(77)

@cpp_template("::tpy::Task<int32_t>::from_coro({0})")
def task_from_sub[T](coro: T) -> Task[Int32]: ...

async def caller() -> Int32:
    t = task_from_sub(sub())
    return await t

async def main_coro() -> None:
    result = await caller()
    print(result)

def main() -> None:
    asyncio.run(main_coro())

main()
