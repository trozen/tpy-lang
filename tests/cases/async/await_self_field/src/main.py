# Regression: `await self.field` (a stored awaitable field) must mark the
# enclosing async method's receiver mutable -- the field's __poll__ mutates
# through self, so a const receiver would fail to build.
import asyncio
from asyncio import Event


class Gate:
    evt: Event

    def __init__(self) -> None:
        self.evt = Event()

    def open(self) -> None:
        self.evt.set()

    async def passed(self) -> bool:
        await self.evt
        return True


async def opener(g: Gate) -> None:
    g.open()


async def main_coro() -> None:
    g = Gate()
    asyncio.create_task(opener(g))
    r = await g.passed()
    print("passed", r)


def main() -> None:
    asyncio.run(main_coro())


main()
