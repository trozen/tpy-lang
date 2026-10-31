# A borrowing tuple forwarded by `return await` into an owning tuple return is
# refused (BUGS.md#async-return-await-tuple-form-change-rejects).
import asyncio
from tpy import int32, Own


class Box:
    def __init__(self, v: int32) -> None:
        self.v = v


async def pair(b: Box) -> tuple[int32, Box]:
    await asyncio.sleep(0)
    return (1, b)


async def owned(b: Box) -> tuple[int32, Own[Box]]:
    return await pair(b)  # tpyc: error(/res\.return_await_form/)


async def amain() -> None:
    t = await owned(Box(3))
    print(t[0])


asyncio.run(amain())
