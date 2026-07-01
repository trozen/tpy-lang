# A local async def shadowing a from-imported name of the same type must win:
# the awaited coro struct is the LOCAL one, not the imported module's (regression
# -- a naive imported_names lookup would mis-qualify to othermod and print 99).
import asyncio
from othermod import work
from tpy import Int32

async def work() -> Int32:
    await asyncio.sleep(0)
    return 7

async def amain() -> None:
    v = await work()
    print(v)

asyncio.run(amain())
