# Awaiting via a re-export chain: import through `facade` (which re-exports from
# `definer`); the coro struct lives in the DEFINER's namespace, so the qualifier
# must be the chain-flattened definer, not the direct import (regression guard).
import asyncio
from facade import deep

async def amain() -> None:
    v = await deep()
    print(v)

asyncio.run(amain())
