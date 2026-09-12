import asyncio
from typing import overload
from tpy import int32
@overload
async def f(a: int32) -> int32: ...
@overload
async def f(a: str) -> int32: ...
async def f(a: int32 | str) -> int32:
    await asyncio.sleep(0)
    return 1
def main() -> None:
    print(1)
main()
