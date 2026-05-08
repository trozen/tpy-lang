# v1: asyncio.create_task requires a direct call to a known async def
# (see `_require_async_def_call_arg` in tpyc/sema/calls.py).
from tpy import Int32
from asyncio import Future
import asyncio


async def main_coro() -> None:
    f = Future[Int32]()
    f.set_result(Int32(7))
    asyncio.create_task(f)  # tpyc: error(/requires a direct call to an async def/)
