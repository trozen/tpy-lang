# Async methods on user classes. The factory body in the class is a
# thin returner of the coro struct (__coro_<Class>_<method>); self is
# captured as the first ctor arg on the struct.
import asyncio
from tpy import Int32


class Adder:
    base: Int32

    def __init__(self, b: Int32) -> None:
        self.base = b

    async def add(self, x: Int32) -> Int32:
        return self.base + x


async def main_coro() -> None:
    a = Adder(10)
    r = await a.add(5)
    print(r)


asyncio.run(main_coro())
