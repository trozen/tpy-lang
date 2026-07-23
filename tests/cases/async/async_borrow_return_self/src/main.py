# Async borrow-return ABI: `async def -> C: return self` hands back an
# ALIAS of the receiver (pointer Poll payload) at a direct await --
# mutations through the awaited result are visible on the original
# (CPython aliasing). Also covers a field-rooted borrow return.
import asyncio


class Server:
    n: int

    def __init__(self) -> None:
        self.n = 1


class Wrap:
    inner: Server

    def __init__(self) -> None:
        self.inner = Server()

    async def me(self) -> "Wrap":
        await asyncio.sleep(0)
        return self

    async def unwrap(self) -> Server:
        await asyncio.sleep(0)
        return self.inner


async def main() -> None:
    w = Wrap()
    r = await w.me()
    r.inner.n = 99
    print(w.inner.n)
    s = await w.unwrap()
    s.n = 5
    print(w.inner.n)


asyncio.run(main())
