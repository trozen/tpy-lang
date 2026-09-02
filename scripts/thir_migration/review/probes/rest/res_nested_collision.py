import asyncio
from tpy import Int32
def bump(d: Int32) -> Int32:
    return d
async def runner() -> Int32:
    total: Int32 = 0
    def bump(d: Int32) -> Int32:
        nonlocal total
        total += d
        return total
    bump(2)
    await asyncio.sleep(0)
    return bump(3)
def main() -> None:
    print(asyncio.run(runner()))
main()
