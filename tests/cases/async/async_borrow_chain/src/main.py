# `return await inner()` under a borrow contract: a borrow-returning
# await may be re-returned (the pointer payload chains through the outer
# frame); the final result still aliases the original receiver.
import asyncio


class Box2:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v

    async def me(self) -> "Box2":
        await asyncio.sleep(0)
        return self


async def relay(b: Box2) -> Box2:
    return await b.me()


async def main() -> None:
    b = Box2(2)
    r = await relay(b)
    r.v = 77
    print(b.v)


asyncio.run(main())
