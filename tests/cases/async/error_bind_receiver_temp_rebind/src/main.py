# The stable-lvalue receiver rule covers ALL binding forms: a plain
# rebind must not bypass it (the coroutine would hold a reference to a
# destroyed temporary).
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
    c = Counter(99).bump(7)  # tpyc: error(/receiver of a bound async method call must be a stable lvalue/)
    print(await c)


def main() -> None:
    asyncio.run(main_coro())


main()
