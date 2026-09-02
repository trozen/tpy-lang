import asyncio
from tpy import Int32
async def step(n: Int32) -> Int32:
    await asyncio.sleep(0)
    return n
async def f() -> Int32:
    ts = [asyncio.create_task(step(1)), asyncio.create_task(step(2))]
    return await ts[0]
def main() -> None:
    pass
main()
