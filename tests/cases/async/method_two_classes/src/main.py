# Two classes with same-named async method `tag`. Coro struct names
# disambiguate via the owning record (__coro_A_tag vs __coro_B_tag) so
# no collision.
import asyncio


class A:
    async def tag(self) -> str:
        return "from-A"


class B:
    async def tag(self) -> str:
        return "from-B"


async def main_coro() -> None:
    a = A()
    b = B()
    print(await a.tag())
    print(await b.tag())


asyncio.run(main_coro())
