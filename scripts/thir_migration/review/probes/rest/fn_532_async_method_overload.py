import asyncio
from typing import overload
from tpy import Int32
class H:
    n: Int32
    def __init__(self) -> None:
        self.n = 1
    @overload
    async def m(self, a: Int32) -> Int32: ...
    @overload
    async def m(self, a: str) -> Int32: ...
    async def m(self, a: Int32 | str) -> Int32:
        await asyncio.sleep(0)
        return self.n
def main() -> None:
    print(1)
main()
