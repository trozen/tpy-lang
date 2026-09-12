import asyncio
from tpy import int32
async def f(t: tuple[int32, int32]) -> int32:
    await asyncio.sleep(0)
    a, b = t
    return a + b
def main() -> None:
    pass
main()
