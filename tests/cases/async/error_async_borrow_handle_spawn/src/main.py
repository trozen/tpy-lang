# A BOUND borrow-returning coroutine handle (ConcreteCoroType carrying
# result_is_borrow) passed to create_task: the erasure reject must fire on
# the named-handle shape, not only the direct-call argument.
import asyncio


class C:
    v: int

    def __init__(self) -> None:
        self.v = 1

    async def get(self) -> "C":
        await asyncio.sleep(0)
        return self


async def main() -> None:
    c = C()
    h = c.get()
    t = asyncio.create_task(h)  # tpyc: error(/borrow-returning coroutine/)
    r = await t
    print(r.v)


asyncio.run(main())
