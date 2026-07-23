# A borrow return under a SUSPENDING finally: the pending-return slot is
# the pointer payload, so the finally's mutation of the receiver is
# visible through the awaited result (CPython aliasing -- contrast the
# storage-form eager-copy divergence tracked in BUGS.md).
import asyncio


class Counter:
    n: int

    def __init__(self) -> None:
        self.n = 1

    async def grab(self) -> "Counter":
        try:
            return self
        finally:
            await asyncio.sleep(0)
            self.n = 10


async def main() -> None:
    c = Counter()
    r = await c.grab()
    print(r.n)
    print(c.n)


asyncio.run(main())
