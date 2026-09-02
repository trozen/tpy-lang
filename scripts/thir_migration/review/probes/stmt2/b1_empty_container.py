import asyncio
from tpy import Int32, Own

async def f() -> Own[list[Int32]]:
    await asyncio.sleep(0)
    return []

def main() -> None:
    pass
main()
