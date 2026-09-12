import asyncio
from tpy import int32
def bump(d: int32) -> int32:
    return d
async def runner() -> int32:
    total: int32 = 0
    def bump(d: int32) -> int32:
        nonlocal total
        total += d
        return total
    bump(2)
    await asyncio.sleep(0)
    return bump(3)
def main() -> None:
    print(asyncio.run(runner()))
main()
