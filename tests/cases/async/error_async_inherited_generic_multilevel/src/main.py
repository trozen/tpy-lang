# An inherited async method through a multi-level generic chain is rejected
# with a clear diagnostic (the grandparent's type param is not bound). async
# with / async for route through the same codegen guard.
import asyncio
from tpy import int32


class A[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v

    async def fetch(self) -> T:
        await asyncio.sleep(0.001)
        return self.v


class B[U](A[U]):
    def __init__(self, v: U) -> None:
        self.v = v


class C(B[int32]):
    def __init__(self, v: int32) -> None:
        self.v = v


async def main_coro() -> None:
    c = C(5)
    r = await c.fetch()  # tpyc: error(/type parameters are not bound to concrete types/)
    print("got:", r)


def main() -> None:
    asyncio.run(main_coro())


main()
