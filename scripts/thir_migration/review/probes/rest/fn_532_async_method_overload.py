import asyncio
from typing import overload
from tpy import int32
class H:
    n: int32
    def __init__(self) -> None:
        self.n = 1
    @overload
    async def m(self, a: int32) -> int32: ...
    @overload
    async def m(self, a: str) -> int32: ...
    async def m(self, a: int32 | str) -> int32:
        await asyncio.sleep(0)
        return self.n
def main() -> None:
    print(1)
main()
