# Awaiting a cross-module async def namespaces the sub-coro struct to the callee
# module. Covers bare + aliased + qualified from-import awaits and a same-module
# await (which must stay bare, not over-qualified).
import asyncio
import asyncmod
from asyncmod import ping
from asyncmod import ping as aliased
from tpy import int32

async def local_double(x: int32) -> int32:
    await asyncio.sleep(0)
    return x * 2

async def amain() -> None:
    v1 = await ping()              # bare from-import (the reproducer)
    v2 = await aliased()           # aliased from-import
    v3 = await asyncmod.add(2, 3)  # qualified (inverse: must still work)
    v4 = await local_double(21)    # same-module (must not be over-qualified)
    print(v1)
    print(v2)
    print(v3)
    print(v4)

asyncio.run(amain())
