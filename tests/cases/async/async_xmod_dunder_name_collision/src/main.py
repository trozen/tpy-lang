# The three owner-typed frame embeddings, each reaching a CROSS-MODULE callee
# whose bare name collides with a local record that implements the same dunder
# as a coro. Those structs live in svc.py's namespace and are already complete
# via its header, so none is an emit-ordering edge here -- but keying an edge on
# the bare `(method, record)` pair resolves each to the LOCAL unit, producing a
# phantom self-edge and rejecting valid code as a recursive embedding.
import asyncio
import svc
from tpy import int32


class Gate:
    # `async with` embeds __aenter__/__aexit__; the manager is svc.Gate.
    async def __aenter__(self) -> int32:
        async with svc.Gate() as v:
            return v + 1

    async def __aexit__(self, et: None, ev: None, tb: None) -> None:
        await asyncio.sleep(0)


class Ticker:
    total: int32

    def __init__(self) -> None:
        self.total = 0

    # `async for` embeds the source's __anext__ coro; the source is svc.Ticker.
    async def __anext__(self) -> int32:
        async for v in svc.Ticker(3):
            self.total += v
        return self.total


class Svc:
    # An owner-typed inline await embeds the callee coro; the owner is svc.Svc.
    async def fetch(self) -> int32:
        h = svc.Svc()
        return await h.fetch()


async def amain() -> None:
    async with Gate() as v:
        print(v)
    t = Ticker()
    print(await t.__anext__())
    s = Svc()
    print(await s.fetch())


asyncio.run(amain())
