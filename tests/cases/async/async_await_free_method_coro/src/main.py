# Regression guard for the historical ordering: a free coroutine awaiting a
# METHOD coroutine (the case the old method-first emission was built for). The
# dependency-ordered emission still puts the method struct before its free
# awaiter.
import asyncio
from tpy import int32


class Svc:
    async def fetch(self) -> int32:
        await asyncio.sleep(0)
        return 7


async def driver() -> None:
    s = Svc()
    v = await s.fetch()
    print(v)


async def main_coro() -> None:
    await driver()


def main() -> None:
    asyncio.run(main_coro())


main()
