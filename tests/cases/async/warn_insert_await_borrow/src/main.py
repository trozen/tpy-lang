# The insert-slot half of warn_return_await_borrow_own: an awaited borrow at
# an element slot copies with a warning rather than an error. The copy diverges
# from CPython (which aliases), so the inserted element is deliberately not
# observed after mutating the source -- the warning is the subject. The
# explicit `copy(await ...)` the warning names is pinned by
# async/return_own_await_borrow_copy.
import asyncio

from tpy import int32


class Payload:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    p: Payload

    def __init__(self, v: int32) -> None:
        self.p = Payload(v)

    async def borrow(self) -> Payload:
        return self.p


async def amain() -> None:
    h = Holder(1)
    xs: list[Payload] = []
    xs.append(await h.borrow())  # tpyc: warning(/copies Payload into owned storage/)
    print(xs[0].v)


asyncio.run(amain())
