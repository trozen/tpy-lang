# The spelling warn_return_await_borrow_own's diagnostic names: an AWAITED
# borrow copied into an `Own[T]` return. The awaited result is a pointer into
# caller-durable storage, so the copy has to read through it -- both the
# one-step `copy(await ...)` and the two-step over the pointer-local the await
# binds. COPY SEMANTICS ARE THE POINT: the source is mutated after each return
# and the copies keep their old value, which CPython agrees with because
# `copy()` deep-copies there.
import asyncio

from tpy import int32, Own, copy


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


async def take_one_step(h: Holder) -> Own[Payload]:
    return copy(await h.borrow())  # tpyc: ok


async def take_two_step(h: Holder) -> Own[Payload]:
    x = await h.borrow()
    return copy(x)  # tpyc: ok


async def amain() -> None:
    h = Holder(1)
    a = await take_one_step(h)
    b = await take_two_step(h)
    h.p.v = 9
    print(h.p.v, a.v, b.v)


asyncio.run(amain())
