# Two local coros, each inline-awaiting a SAME-NAMED coro from another module.
# The struct each embeds is `helper::__coro_step` / `helper::__coro_other`, which
# the included header already defines -- so neither is an ordering edge here.
# Keying the edges on the bare name instead would read this as `step` awaiting
# the LOCAL `step`, a phantom cycle rejected as "recursive coroutine embedding".
import asyncio
import helper
from tpy import Int32


async def step() -> Int32:
    return await helper.other()


async def other() -> Int32:
    return await helper.step()


async def amain() -> None:
    print(await step(), await other())


asyncio.run(amain())
