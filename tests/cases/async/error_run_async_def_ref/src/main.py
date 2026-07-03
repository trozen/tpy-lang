# Passing the async def itself (missing the call) to asyncio.run gets a
# targeted hint instead of a generic inference failure.
from asyncio import run


async def add_one(n: int) -> int:
    return n + 1


def main() -> None:
    run(add_one)  # tpyc: error(/not the async def itself/)


main()
