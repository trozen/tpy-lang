# 'await' in a match-case guard is rejected with a workaround hint
# (a faithful rewrite would decompose the whole match).
import asyncio
from tpy import int32


async def allowed(n: int32) -> bool:
    return n > 0


async def go(n: int32) -> None:
    match n:
        case 1 if await allowed(1):  # tpyc: error(/'await' in a match-case guard is not supported/)
            print("one")
        case _:
            print("other")


asyncio.run(go(1))
