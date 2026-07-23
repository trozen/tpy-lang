# create_task erases to an owned handle whose result outlives the
# statement -- a borrow-returning coroutine must be rejected (declare
# Own[C] instead). Covers the direct-call arg shape.
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
    t = asyncio.create_task(c.get())  # tpyc: error(/borrow-returning coroutine/)
    r = await t
    print(r.v)


asyncio.run(main())
