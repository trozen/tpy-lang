import asyncio
from tpy import Int32
async def f(t: tuple[Int32, Int32]) -> Int32:
    await asyncio.sleep(0)
    a, b = t
    return a + b
def main() -> None:
    pass
main()
