# `return await make()` under a bare `-> C` contract: the awaited result
# is an owned temporary in the frame, which cannot be handed out as a
# borrow -- same rule and message as sync's local/temporary reject.
import asyncio
from tpy import Own


class C:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


async def make() -> Own[C]:
    await asyncio.sleep(0)
    return C(3)


async def outer() -> C:
    return await make()  # tpyc: error(/Cannot return local or temporary/)


async def main() -> None:
    r = await outer()
    print(r.v)


asyncio.run(main())
