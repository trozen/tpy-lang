# An `Own[<static protocol>]` argument at an AWAIT position rejects; the frame's
# own param capture is fine. BUGS.md#own-static-protocol-frame-capture-borrows
import asyncio
from tpy import Own, Int32
from typing import Protocol


class Sink(Protocol):
    def get(self) -> Int32: ...


class Printer:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def get(self) -> Int32:
        return self.n


async def inner(s: Own[Sink]) -> Int32:
    await asyncio.sleep(0)
    return 1


async def outer(s: Own[Sink]) -> Int32:  # tpyc: error(/res\.await_param_type:own_protocol\.static/)
    # The sub-future field is spelled off the argument expression, so this
    # forward of an lvalue would make the sub-frame borrow what the slot owns.
    v = await inner(s)
    return v + 1


async def amain() -> None:
    p = Printer(41)
    print(await outer(p))


asyncio.run(amain())
