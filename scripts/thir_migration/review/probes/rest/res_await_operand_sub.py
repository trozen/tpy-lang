import asyncio
from tpy import int32
async def step(n: int32) -> int32:
    await asyncio.sleep(0)
    return n
async def f() -> int32:
    ts = [asyncio.create_task(step(1)), asyncio.create_task(step(2))]
    return await ts[0]
def main() -> None:
    pass
main()
