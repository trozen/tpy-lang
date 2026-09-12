# Regression: `async with` where __aenter__ and __aexit__ are defined on
# DIFFERENT ancestors -- each coro struct names its own defining record (the
# owners are stamped independently).
import asyncio
from tpy import int32


class HasEnter:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    async def __aenter__(self) -> None:
        await asyncio.sleep(0)
        self.n += 1


class HasBoth(HasEnter):
    async def __aexit__(self, et: None, ev: None, tb: None) -> None:
        await asyncio.sleep(0)
        self.n += 100


class Mgr(HasBoth):
    pass


async def main_coro() -> None:
    m = Mgr()
    async with m:
        print("inside:", m.n)
    print("after:", m.n)


def main() -> None:
    asyncio.run(main_coro())


main()
