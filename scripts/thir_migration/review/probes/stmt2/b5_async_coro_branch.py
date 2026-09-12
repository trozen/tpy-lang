import asyncio
from tpy import int32
async def step(n: int32) -> int32:
    await asyncio.sleep(0)
    return n
async def f(n: int32, flag: bool) -> int32:
    d = step(n)
    await asyncio.sleep(0)
    if flag:
        d = step(n + 1)
    return await d
def main() -> None:
    pass
main()
