# Binding a borrow-returning coroutine whose callee has a static-protocol
# param forces the type-erased handle (the concrete frame struct needs
# call-site deduction), and an erased handle's result must be owned --
# rejected with the Own fix-hint.
import asyncio
from typing import Iterable
from tpy import Int32


class C:
    v: int

    def __init__(self) -> None:
        self.v = 1


async def pick(c: C, xs: Iterable[Int32]) -> C:
    await asyncio.sleep(0)
    return c


async def main() -> None:
    c = C()
    h = pick(c, [1, 2])  # tpyc: error(/cannot bind this coroutine to a handle/)
    r = await h
    print(r.v)


asyncio.run(main())
