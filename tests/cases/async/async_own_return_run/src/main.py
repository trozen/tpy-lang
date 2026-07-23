# `async def -> Own[C]` results flow through every owned consumer:
# asyncio.run, create_task + await, and wait_for (the Own is normalized
# out of the Cancellable type arg, so Own[Cancellable[T]] slots match).
import asyncio
from tpy import Own


class Box2:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


async def make(v: int) -> Own[Box2]:
    await asyncio.sleep(0)
    return Box2(v)


async def main() -> None:
    t = asyncio.create_task(make(1))
    a = await t
    print(a.v)
    b = await asyncio.wait_for(make(2), 5.0)
    print(b.v)


direct = asyncio.run(make(3))
print(direct.v)
asyncio.run(main())
