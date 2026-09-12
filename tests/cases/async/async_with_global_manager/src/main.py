# `async with g:` on a module-global async context manager (a global
# already renders as the `CM*` the borrowed frame field wants, so the
# bind must not re-take its address). The manager mutates its own state
# across the region; observing the count through the global afterward
# forces the borrow -- a silent copy would lose it.
import asyncio
from tpy import int32


class Counter:
    opens: int32

    def __init__(self) -> None:
        self.opens = 0

    async def __aenter__(self) -> int32:
        self.opens += 1
        return self.opens

    async def __aexit__(self, exc_type: None, exc_val: None,
                        exc_tb: None) -> None:
        pass


g = Counter()


async def main_coro() -> None:
    async with g as n1:
        print(n1)
    async with g as n2:
        print(n2)
    print(g.opens)


def main() -> None:
    asyncio.run(main_coro())


main()
