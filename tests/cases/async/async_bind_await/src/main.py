# `await c` on a bound coroutine (erased await through the owned handle);
# covers free-function and method coroutines. The await consumes the handle.
import asyncio


class Counter:
    base: int

    def __init__(self, base: int) -> None:
        self.base = base

    async def bump(self, n: int) -> int:
        return self.base + n


async def add_one(n: int) -> int:
    return n + 1


async def main_coro() -> None:
    c = add_one(1)
    print(await c)
    w = Counter(10)
    m = w.bump(5)
    print(await m)


def main() -> None:
    asyncio.run(main_coro())


main()
