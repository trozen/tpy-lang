import asyncio
from tpy import Int32
async def step(n: Int32) -> Int32:
    await asyncio.sleep(0)
    return n
async def f(n: Int32, flag: bool) -> Int32:
    d = step(n)
    await asyncio.sleep(0)
    if flag:
        d = step(n + 1)
    return await d
def main() -> None:
    pass
main()
