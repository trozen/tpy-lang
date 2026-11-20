# A body last use copies, as the hand-written function does, so a read the
# compiler might take for dead still sees the value: a constructor reading its
# parameter after a member took it, an inherited field and a field of its own
# set from one parameter, a protocol method returning a plain `str` before a
# later use. `readonly[T]` frames at a class bind the caller's object.
# `Send[T]` / `Sync[T]` parameters: reassigned ones at `str` and `bytes`, a
# shim's at a ValueType.
import asyncio
from typing import Iterator
from tpy import Send, Sync, ValueType, int32, readonly
from fixtures import C, Labeled, V


def count[T](x: T) -> int32:
    return 1


class Two[T]:
    # a generic call's result does not declare a field by itself
    n: int32

    def __init__(self, v: T) -> None:
        # the first member's read is not the last: `v` is read again
        self.a = v  # tpyc: warning(/may copy T into field/)
        self.b = v  # tpyc: warning(/may copy T into field/)
        # and once more in the body, after both members
        self.n = count(v)


class Base[T]:
    def __init__(self, v: T) -> None:
        self.bv = v  # tpyc: warning(/may copy T into field/)


class Derived[T](Base[T]):
    def __init__(self, v: T) -> None:
        super().__init__(v)
        # the inherited field took `v` first; this one still reads it
        self.av = v  # tpyc: warning(/may copy T into field/)


class Direct[T](Base[T]):
    # no `super().__init__` on purpose: the inherited field is set here
    def __init__(self, v: T) -> None:  # tpyc: warning(/does not call 'super\(\)\.__init__/)
        # the inherited field is set in the body, after the member-init of
        # `av` read `v`: that read must not have taken the value
        self.bv = v  # tpyc: warning(/may copy T into field/)
        self.av = v  # tpyc: warning(/may copy T into field/)


def label_then_use[T: Labeled](x: T) -> str:
    # a plain `str` result lends nothing of `x`, which is read after it
    s = x.label()
    return s + "/" + x.label()


def ro_gen[T](x: readonly[T]) -> Iterator[readonly[T]]:
    yield x


async def ro_co[T](x: readonly[T]) -> readonly[T]:
    await asyncio.sleep(0)
    return x


def rebind_send[T: ValueType](x: Send[T], y: Send[T]) -> T:
    # the prologue and the assignment of a reassigned `Send[T]` parameter
    x = y
    return x


def rebind_sync[T: ValueType](x: Sync[T], y: Sync[T]) -> T:
    x = y
    return x


class Bag[T]:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __getitem__(self, k: Send[T]) -> int32:
        return self.n


async def co_main() -> None:
    c = C(1)
    f = ro_co(c)
    c.n = 7
    r = await f
    # the frame borrowed `c`: the write after creation shows
    print("ro async class", r.n, r is c)


def main() -> None:
    t = Two("two")
    print("reads str", t.a, t.b, t.n)
    tv = Two(V("tv"))
    print("reads V", tv.a.s, tv.b.s)
    d = Derived(V("dv"))
    print("reads inherited", d.bv.s, d.av.s)
    dd = Direct(V("dd"))
    print("reads inherited direct", dd.bv.s, dd.av.s)
    print("reads label", label_then_use(V("lab")))
    c = C(1)
    g = ro_gen(c)
    c.n = 5
    for e in g:
        print("ro generator class", e.n, e is c)
    asyncio.run(co_main())
    print("send rebind str", rebind_send("a", "b"))
    print("send rebind bytes", rebind_send(b"a", b"b"))
    print("sync rebind", rebind_sync("c", "d"), rebind_sync(b"e", b"f"))
    k = V("sk")
    print("send shim", Bag[V](3)[k], k.s)
