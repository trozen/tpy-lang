# The receiver of a bound method-coroutine is borrowed until the handle
# is consumed: an owning move of the receiver in between demotes to a
# copy (with the explicit-copy warning) instead of moving storage out
# from under the live handle's reference.
import asyncio


class Counter:
    base: int

    def __init__(self, base: int) -> None:
        self.base = base

    async def bump(self, n: int) -> int:
        return self.base + n


async def main_coro() -> None:
    lst: list[Counter] = []
    w = Counter(40)
    c = w.bump(5)
    lst.append(w)  # tpyc: warning(/copies Counter into owned storage/)
    print(await c)


def main() -> None:
    asyncio.run(main_coro())


main()
