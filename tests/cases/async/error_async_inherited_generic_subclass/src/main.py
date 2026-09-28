# A generic subclass of a generic base (Child[U](Box[U])) instantiated
# concretely: the inherited async method's coro-struct owner has an unbound
# type param (mro_ancestors carries Child's U, not the instantiation's int32),
# so it is rejected with a clear diagnostic. Same guard as the multi-level case.
import asyncio
from tpy import int32


class Box[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v

    async def fetch(self) -> T:
        await asyncio.sleep(0.001)
        return self.v


class Child[U](Box[U]):
    def __init__(self, v: U) -> None:
        super().__init__(v)


async def main_coro() -> None:
    c = Child[int32](7)
    r = await c.fetch()  # tpyc: error(/type parameters are not bound to concrete types/)
    print("got:", r)


def main() -> None:
    asyncio.run(main_coro())


main()
