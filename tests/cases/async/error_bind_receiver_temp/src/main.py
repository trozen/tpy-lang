# Binding an async method call on a temporary receiver is rejected: the
# coroutine captures the receiver by reference for the handle's lifetime
# (fixes the dangling-receiver gap for both concrete and erased handles).
import asyncio


class Counter:
    base: int

    def __init__(self, base: int) -> None:
        self.base = base

    async def bump(self, n: int) -> int:
        return self.base + n


async def main_coro() -> None:
    c = Counter(40).bump(5)  # tpyc: error(/receiver of a bound async method call must be a stable lvalue/)
    print(await c)


def main() -> None:
    asyncio.run(main_coro())


main()
