# The coroutine twin of error_dispatch_generator_rebind_variant: one name
# cannot hold the coroutines of two variants of a @dispatch set, though
# CPython runs it (BUGS.md#dispatch-generator-rebind-variant).
import asyncio
from tpy import dispatch, int32


@dispatch
async def co(x: int32) -> int32:
    return x


@dispatch
async def co(x: str) -> int32:
    return len(x)


async def amain() -> None:
    h = co(1)
    print(await h)
    h = co("ab")  # tpyc: error(/cannot rebind 'h' to a different coroutine: 'h' holds a 'co\(x: int32\)' coroutine.*bind 'co\(x: str\)' to a new name/)
    print(await h)


asyncio.run(amain())
