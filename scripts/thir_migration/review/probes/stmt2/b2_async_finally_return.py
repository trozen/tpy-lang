import asyncio
from tpy import Int32
async def f(n: Int32) -> Int32:
    try:
        await asyncio.sleep(0)
    finally:
        return n
def main() -> None:
    pass
main()
