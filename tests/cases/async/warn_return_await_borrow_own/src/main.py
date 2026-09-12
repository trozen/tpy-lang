# An AWAITED borrow at an `Own[T]` return is the async spelling of
# warn_return_record_borrow_method_own: awaiting a coroutine whose declared
# return is a bare reference type hands back a pointer into the awaitee's
# storage, so the owning slot copies and warns. Before sema read the await as
# a borrowed source this emitted ill-formed C++ with no location (a `Payload*`
# into `own_param_t<Payload>`). The copy is the ACKNOWLEDGED CPython
# divergence, so the case prints only what both agree on and the WARNING is
# the pin; `take_copy` is the spelling that silences it.
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


async def take(h: Holder) -> Own[Payload]:
    return await h.borrow()  # tpyc: warning(/copies Payload into owned storage/)


async def take_copy(h: Holder) -> Own[Payload]:
    return copy(await h.borrow())  # tpyc: ok


async def amain() -> None:
    h = Holder(1)
    p = await take(h)
    q = await take_copy(h)
    print(p.v, q.v, h.p.v)


asyncio.run(amain())
