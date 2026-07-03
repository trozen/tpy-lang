# Moving a bound METHOD-coroutine handle (frame holds a receiver
# reference): name-source writes must move-construct via emplace --
# optional's move-assign is deleted for frames with reference members.
import asyncio


class Counter:
    base: int

    def __init__(self, base: int) -> None:
        self.base = base

    async def bump(self, n: int) -> int:
        return self.base + n


async def main_coro() -> None:
    w = Counter(40)
    c = w.bump(5)
    d = c
    print(await d)


def main() -> None:
    asyncio.run(main_coro())


main()
