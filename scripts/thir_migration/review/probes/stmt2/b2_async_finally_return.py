import asyncio
from tpy import int32
async def f(n: int32) -> int32:
    try:
        await asyncio.sleep(0)
    finally:
        return n
def main() -> None:
    pass
main()
