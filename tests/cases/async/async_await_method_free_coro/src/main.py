# A concrete (param-less, non-templated) async method awaiting a free-function
# coroutine. The method's coro struct embeds `optional<__coro_helper>`, which
# needs __coro_helper complete; coro structs are now emitted in inline-await
# dependency order (awaited before awaiter), so this compiles. Previously the
# method struct was emitted before the free coro -> incomplete-type C++ error.
import asyncio
from tpy import Int32


async def helper() -> Int32:
    await asyncio.sleep(0)
    return 42


class Runner:
    async def run(self) -> None:
        v = await helper()
        print(v)


async def main_coro() -> None:
    r = Runner()
    await r.run()


def main() -> None:
    asyncio.run(main_coro())


main()
