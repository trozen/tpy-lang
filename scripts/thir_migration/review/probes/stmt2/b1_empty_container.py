import asyncio
from tpy import int32, Own

async def f() -> Own[list[int32]]:
    await asyncio.sleep(0)
    return []

def main() -> None:
    pass
main()
