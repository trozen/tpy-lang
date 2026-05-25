# asyncio.gather is homogeneous: all positional task args must share T.
# Mixing Task[Int32] with Task[str] must produce a clean tpyc diagnostic
# at the call site rather than a cryptic C++ template error.
import asyncio
from tpy import Int32


async def fetch_int() -> Int32:
    await asyncio.sleep(0.0)
    return 42


async def fetch_str() -> str:
    await asyncio.sleep(0.0)
    return "hello"


async def main_coro() -> None:
    t_int = asyncio.create_task(fetch_int())
    t_str = asyncio.create_task(fetch_str())
    await asyncio.gather(t_int, t_str)  # tpyc: error(/Cannot infer type arguments/)


def main() -> None:
    asyncio.run(main_coro())


main()
