# Regression: `async with` on a reference-type lvalue manager must borrow it
# (a `T*` frame field), so __aenter__/__aexit__ act on the original, including
# on the body-raises path. Mutate-and-observe.
import asyncio


class Counter:
    n: int

    def __init__(self) -> None:
        self.n = 0

    async def __aenter__(self) -> None:
        await asyncio.sleep(0)
        self.n += 1

    async def __aexit__(self, exc_type: None, exc_val: None,
                        exc_tb: None) -> None:
        await asyncio.sleep(0)
        self.n += 100


async def guard_scope(c: Counter) -> None:
    async with c:
        print("inside:", c.n)
    print("after:", c.n)


async def raise_scope(c: Counter) -> None:
    # __aexit__ must fire on the borrowed original even when the body raises.
    try:
        async with c:
            raise ValueError("boom")
    except ValueError:
        print("caught, n:", c.n)


async def main_coro() -> None:
    c = Counter()
    await guard_scope(c)
    print("final:", c.n)
    d = Counter()
    await raise_scope(d)
    print("raised final:", d.n)


def main() -> None:
    asyncio.run(main_coro())


main()
