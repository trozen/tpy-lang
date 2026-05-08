# v1: asyncio.run requires a direct call to a known async def
# (see `_require_async_def_call_arg` in tpyc/sema/calls.py).
from tpy import Int32
from asyncio import Future, run


def main() -> None:
    f = Future[Int32]()
    f.set_result(Int32(0))
    run(f)  # tpyc: error(/requires a direct call to an async def/)


main()
