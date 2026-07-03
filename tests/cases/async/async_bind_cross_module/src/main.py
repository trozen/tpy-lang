# Binding a coroutine from an IMPORTED async def: the concrete frame
# slot's struct name is qualified with the callee module's namespace
# (the module_qual naming path). Covers bare-import and from-import.
import asyncio
import helpers
from helpers import add_one


async def main_coro() -> None:
    c = add_one(41)
    print(await c)
    d = helpers.add_one(9)
    t = asyncio.create_task(d)
    print(await t)


def main() -> None:
    asyncio.run(main_coro())


main()
