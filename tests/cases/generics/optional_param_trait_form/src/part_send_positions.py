# A `Send[T] | None` / `Sync[T] | None` parameter at a coroutine, a method, a
# constructor and a generator reads its None test as the bare `T | None` does
# (storing it into a `T | None` field is a separate, refused shape:
# BUGS.md#generic-send-optional-to-optional-mismatch).
import asyncio
from typing import Iterator

from tpy import Send, Sync, ValueType, readonly

from fixtures import P, V


async def asend[T](x: Send[T] | None) -> bool:  # tpyc: frame_send(no)
    return x is not None  # tpyc: ok


async def async_sync[T](x: Sync[T] | None) -> bool:  # tpyc: frame_send(no)
    return x is not None  # tpyc: ok


def gen_sync[T](x: Sync[T] | None) -> Iterator[bool]:  # tpyc: frame_send(no)
    yield x is not None  # tpyc: ok


class Probe[T]:
    has: bool

    def __init__(self, x: Send[T] | None) -> None:
        self.has = x is not None  # tpyc: ok

    def probe(self, x: Send[T] | None) -> bool:
        return x is not None  # tpyc: ok


class SyncProbe[T]:
    has: bool

    def __init__(self, x: Sync[T] | None) -> None:
        self.has = x is not None  # tpyc: ok

    def probe(self, x: Sync[T] | None) -> bool:
        return x is not None  # tpyc: ok


def target_send[T](x: Send[T] | None) -> bool:
    return x is not None


def target_sync[T](x: Sync[T] | None) -> bool:
    return x is not None


def target_send_ro[T](x: Send[readonly[T]] | None) -> bool:
    return x is not None


def fwd[T](x: Send[T] | None, y: Sync[T] | None,
           z: Send[readonly[T]] | None) -> bool:
    # A generic body forwards each qualified parameter to the same slot.
    return target_send(x) and target_sync(y) and target_send_ro(z)  # tpyc: ok


def fwd_bounded[T: ValueType](x: Send[T] | None, y: Sync[T] | None,
                              z: Send[readonly[T]] | None) -> bool:
    # The ValueType-bounded twin of the forward.
    return target_send(x) and target_sync(y) and target_send_ro(z)  # tpyc: ok


async def amain() -> None:
    p = P(6)
    # A ValueType record argument is refused here, as at a bare `T | None`
    # (BUGS.md#await-open-optional-valuetype-arg-refused).
    print("send_async", await asend(1), await asend("s"), await asend(p))
    print("sync_async", await async_sync(1), await async_sync("s"),
          await async_sync(p))


def main() -> None:
    p = P(7)
    print("send_ctor", Probe(1).has, Probe(V(2)).has, Probe(p).has)
    print("send_method", Probe(1).probe(2), Probe(p).probe(p))
    sp = SyncProbe(1)  # tpyc: is_send(yes) is_sync(yes)
    print("sync_ctor", sp.has, SyncProbe(V(2)).has, SyncProbe(p).has)
    print("sync_method", SyncProbe(1).probe(2), SyncProbe(p).probe(p))
    print("sync_gen", list(gen_sync(1)), list(gen_sync(V(2))),
          list(gen_sync("s")), list(gen_sync(p)))
    print("fwd", fwd(1, 2, 3), fwd(V(1), V(2), V(3)), fwd("a", "b", "c"),
          fwd(p, p, p))
    print("fwd_bounded", fwd_bounded(1, 2, 3), fwd_bounded(V(1), V(2), V(3)),
          fwd_bounded("a", "b", "c"))
    asyncio.run(amain())
