# Regression: `async with` over a subclass that inherits __aenter__/__aexit__
# must (a) find the inherited methods and (b) name their coro structs after
# the defining base. Mutate-and-observe on the shared manager.
import asyncio
from tpy import int32


class BaseCM:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    async def __aenter__(self) -> None:
        await asyncio.sleep(0)
        self.n += 1

    async def __aexit__(self, et: None, ev: None, tb: None) -> None:
        await asyncio.sleep(0)
        self.n += 100


class DerivedCM(BaseCM):
    pass


async def main_coro() -> None:
    cm = DerivedCM()
    async with cm:
        print("inside:", cm.n)
    print("after:", cm.n)


def main() -> None:
    asyncio.run(main_coro())


main()
