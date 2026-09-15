# H1: `await` inside a `match` arm, plus a capture binding (`v`) read after
# the await in the same arm -- the binding is a frame field on the coroutine.
# Also an Optional subject on the CHAIN tier (the None arm is not a prefix,
# so the null-split partition does not apply): the chain carries the
# resumable dispatch hook, so its arm bodies suspend like any other.
import asyncio
from typing import Optional

from tpy import int32


async def sub(n: int) -> int:
    return n * 10


async def caller(tag: int) -> int:
    match tag:
        case 0:
            return await sub(1)
        case v:
            r = await sub(v)
            return r + v


async def opt_chain(o: Optional[int32]) -> int32:
    match o:  # tpyc: ok
        case 1:
            await sub(1)
            return 1
        case None:
            return 0
        case _:
            await sub(2)
            return 2


def main() -> None:
    print(asyncio.run(caller(0)))
    print(asyncio.run(caller(5)))
    print(asyncio.run(opt_chain(1)), asyncio.run(opt_chain(None)),
          asyncio.run(opt_chain(7)))


main()
