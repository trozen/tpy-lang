# Regression: a subclass that OVERRIDES an async method must use its own coro
# struct, not rebase to the base's (guards the owning==receiver branch).
import asyncio
from tpy import Int32


class Base:
    async def val(self) -> Int32:
        await asyncio.sleep(0)
        return 1


class Derived(Base):
    async def val(self) -> Int32:
        await asyncio.sleep(0)
        return 2


async def main_coro() -> None:
    b = Base()
    d = Derived()
    print(await b.val())
    print(await d.val())


def main() -> None:
    asyncio.run(main_coro())


main()
