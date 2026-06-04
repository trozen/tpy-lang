# A free coroutine awaiting another free coroutine defined LATER in the module.
# Source order would emit the awaiter before the callee -> incomplete-type
# error; the dependency-ordered emission puts the callee's struct first.
import asyncio
from tpy import Int32


async def driver() -> None:
    v = await helper()
    print(v)


async def helper() -> Int32:
    await asyncio.sleep(0)
    return 42


async def main_coro() -> None:
    await driver()


def main() -> None:
    asyncio.run(main_coro())


main()
