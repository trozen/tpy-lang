# A borrow-returning coroutine into asyncio.wait_for's Own[Cancellable[T]]
# param: same direct-await-only reject as run/create_task.
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
    r = await asyncio.wait_for(c.get(), 1.0)  # tpyc: error(/borrow-returning coroutine/)
    print(r.v)


asyncio.run(main())
