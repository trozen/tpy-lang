# The receiver of an awaited method is the object the sync member call of
# the same method runs on, whatever reaches it: a None check, a `Ptr`, a
# Deref wrapper, a narrowed union member, a readonly reference.
import asyncio

from tpy import Ptr, int32, nocopy, readonly
from tplib.box import Box


@nocopy
class Acc:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    async def add(self, x: int32) -> int32:
        await asyncio.sleep(0.0)
        # A write through the receiver, read back by the caller afterwards.
        self.base += x
        return self.base

    def sync_add(self, x: int32) -> int32:
        self.base += x
        return self.base

    @readonly
    async def read(self) -> int32:
        await asyncio.sleep(0.0)
        return self.base


@nocopy
class Other:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def mk_ptr(a: Acc) -> Ptr[Acc]:
    return a


async def opt_recv(o: Acc | None) -> int32:
    # An unproven Optional receiver: checked as its sync twin is.
    return await o.add(4)  # tpyc: warning(/Potential None access/)


def opt_sync(o: Acc | None) -> int32:
    return o.sync_add(4)  # tpyc: warning(/Potential None access/)


async def box_recv(b: Box[Acc]) -> int32:
    # Through the wrapper's `__deref__()`, onto the boxed object.
    return await b.add(5)  # tpyc: ok


async def ptr_call_recv(a: Acc) -> int32:
    # A `Ptr` a call returns names the object; nothing fresh to hold.
    return await mk_ptr(a).add(6)  # tpyc: ok


async def union_recv() -> int32:
    # A union local narrowed by its initializer, held by the frame.
    u: Acc | Other = Acc(10)
    r = await u.add(7)  # tpyc: ok
    return r * 100 + u.base


async def isinstance_recv(w: Acc | Other) -> int32:
    if isinstance(w, Acc):
        return await w.add(1)  # tpyc: ok
    return 0


async def readonly_recv(r: readonly[Acc]) -> int32:
    return await r.read()  # tpyc: ok


async def readonly_bound(r: readonly[Acc]) -> int32:
    # A handle bound off a readonly receiver is the concrete coroutine.
    c = r.read()  # tpyc: ok
    return await c


def main() -> None:
    a = Acc(1)
    print("forms_opt", asyncio.run(opt_recv(a)), opt_sync(a), a.base)
    try:
        asyncio.run(opt_recv(None))
    except AttributeError:
        print("forms_opt_none AttributeError")
    try:
        opt_sync(None)
    except AttributeError:
        print("forms_opt_sync_none AttributeError")
    b = Box(Acc(20))
    print("forms_box", asyncio.run(box_recv(b)), b.base)
    print("forms_ptr_call", asyncio.run(ptr_call_recv(a)), a.base)
    print("forms_union", asyncio.run(union_recv()),
          asyncio.run(isinstance_recv(a)),
          asyncio.run(isinstance_recv(Other(0))), a.base)
    print("forms_readonly", asyncio.run(readonly_recv(a)),
          asyncio.run(readonly_bound(a)))
