# A bound async-METHOD coroutine borrows its receiver by reference for
# the handle's lifetime; spawning it as a task escapes the borrow, which
# warns (receiver lifetime is not tracked across the escape). Here the
# receiver outlives the task, so the run is sound.
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
    t = asyncio.create_task(c)  # tpyc: warning(/borrows its receiver/)
    print(await t)


def main() -> None:
    asyncio.run(main_coro())


main()
