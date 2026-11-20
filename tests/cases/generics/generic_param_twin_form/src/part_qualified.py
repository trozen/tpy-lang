# Qualified spellings of a `T` parameter take the bare `T`'s form: a
# `Send[T]` parameter is the callee's own value at `int32` (a body writing the
# argument's field does not change it), and a `Send[T]` dunder parameter binds
# a `str` view; a `readonly[T]` parameter returned at `-> readonly[T]` keeps
# its const at a class instantiation, in a function and a coroutine.
import asyncio
from typing import Callable, Iterator
from tpy import Own, Send, Sync, int32, readonly
from fixtures import Box, C, V


def g_send[T](x: Send[T], b: Box) -> T:
    b.n = 9
    return x


class MutBag[T]:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __call__(self, k: Send[T]) -> int32:
        self.n += 1
        return self.n


def bump_c(c: C) -> None:
    c.n += 1


def bump_sync[T](x: Sync[T], f: Callable[[T], None]) -> None:
    # a `Sync[T]` parameter at a class is the caller's object: the write
    # through it shows in the caller (a method call through `Sync[T]` is
    # refused, BUGS.md#generic-t-marker-spellings-not-unified)
    f(x)


def ret_ro[T](x: readonly[T]) -> readonly[T]:
    return x  # tpyc: ok


def pick_send[T](x: Send[T]) -> T:
    # a `Send[T]` parameter's direct return, as a bare `T`'s
    return x


def ro_send[T](x: readonly[Send[T]]) -> readonly[T]:
    return x


def send_ro[T](x: Send[readonly[T]]) -> readonly[T]:
    return x


def gen_send[T](x: Send[T]) -> Iterator[T]:  # tpyc: frame_send(no)
    yield x


def opt[T](x: T) -> T | None:
    # the pointer-form result aliases the caller's object at a class; at a
    # by-value instantiation it would point at the callee's copy, a filed
    # dangle (BUGS.md#generic-optional-return-committed-to-pointer), so only
    # the class is instantiated here
    return x


def own_ret[T](x: T) -> Own[T]:
    # the direct return moves at a value `T`, as the twin's `return x;`; at a
    # class `T` the parameter is the caller's object, so the owned result is
    # a real copy, hence the hedged warning
    return x  # tpyc: warning(/may copy T into owned storage/)


async def co_send[T](x: Send[T]) -> T:  # tpyc: frame_send(no)
    await asyncio.sleep(0)
    return x


async def aret_ro[T](x: readonly[T]) -> readonly[T]:
    return x


async def co_main(c: C) -> None:
    r = await aret_ro(c)
    print("qualified async readonly", r.n, r is c)
    print("qualified async send", await co_send("cs"))
    sc = C(1)
    f = co_send(sc)
    sc.n = 12
    rs = await f
    # the frame holds the caller's object: the write after creation shows
    print("qualified async send class", rs.n, rs is sc)


def main() -> None:
    b = Box(1)
    # the field face at a `Send[T]` spelling
    print("qualified send field", g_send(b.n, b))
    # at a class the parameter is the caller's object: the result aliases it
    cb = Box(2)
    rb = g_send(cb, cb)
    cb.n = 11
    print("qualified send class", rb.n, rb is cb)
    print("qualified send return", pick_send("s"), pick_send(b"b"), pick_send(V("v")).s)
    pc = C(1)
    rc = pick_send(pc)
    pc.n = 6
    print("qualified send return class", rc.n, rc is pc)
    print("qualified readonly send", ro_send(3), ro_send("r"), send_ro(4), send_ro("q"))
    for e in gen_send("gs"):
        print("qualified send generator", e)
    # a class rvalue: the frame takes the temporary's place
    for ec in gen_send(C(6)):
        print("qualified send generator rvalue", ec.n)
    gc = C(2)
    gg = gen_send(gc)
    gc.n = 13
    for eg in gg:
        print("qualified send generator class", eg.n, eg is gc)
    oc = C(1)
    ro = opt(oc)
    oc.n = 8
    if ro is not None:
        print("qualified opt class", ro.n, ro is oc)
    print("qualified own return", own_ret(V("ov")).s)
    bag = MutBag[str](3)
    print("qualified send call", bag("hello"))
    # a class `T` at the `Send[T]` dunder, handed an rvalue
    cbag = MutBag[C](3)
    print("qualified send call class", cbag(C(1)), cbag.n)
    ct = C(1)
    bump_sync(ct, bump_c)
    print("qualified sync class", ct.n)
    c = C(7)
    r = ret_ro(c)
    print("qualified readonly", r.n, r is c)
    print("qualified readonly value", ret_ro(5), ret_ro("rs"))
    asyncio.run(co_main(c))
