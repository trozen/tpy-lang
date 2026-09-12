# `async with self` inside __aenter__ is a 1-cycle: the frame would store its own
# __aenter__ sub-future by value, which is infinite-size. The async-with edges
# are newly tracked, so this pins that a GENUINE cycle through one still reaches
# the clean located diagnostic instead of a raw C++ incomplete-type error.
import asyncio
from tpy import int32


class Gate:
    async def __aenter__(self) -> int32:  # tpyc: error(/recursive coroutine embedding/)
        async with self as v:
            return v + 1

    async def __aexit__(self, et: None, ev: None, tb: None) -> None:
        await asyncio.sleep(0)


async def amain() -> None:
    g = Gate()
    async with g as v:
        print(v)


asyncio.run(amain())
