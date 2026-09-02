import asyncio
from typing import overload
from tpy import Int32
@overload
async def f(a: Int32) -> Int32: ...
@overload
async def f(a: str) -> Int32: ...
async def f(a: Int32 | str) -> Int32:
    await asyncio.sleep(0)
    return 1
def main() -> None:
    print(1)
main()
