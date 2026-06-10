# Regression: awaiting an inherited async method must name the coro struct
# after the method's defining base, not the calling subclass.
import asyncio
from tpy import Int32


class Pet:
    async def feed(self, n: Int32) -> Int32:
        await asyncio.sleep(0)
        return n + 1


class Dog(Pet):
    pass


async def main_coro() -> None:
    d = Dog()
    v = await d.feed(10)
    print(v)


def main() -> None:
    asyncio.run(main_coro())


main()
