# A tuple async payload with a reference or open-T element aliases the caller's
# objects across suspensions, as the sync tuple return does; Callable too.
import asyncio
from typing import Callable, Iterator, Literal
from tpy import int32, Own, readonly


class Box:
    def __init__(self, v: int32) -> None:
        self.v = v


SHARED = Box(0)


# free function: a borrow tuple
async def pair(b: Box) -> tuple[int32, Box]:
    await asyncio.sleep(0)
    return (1, b)


# free function: an open T element
async def solo[T](x: T) -> tuple[T, int32]:
    await asyncio.sleep(0)
    return (x, 1)


async def mk_fn(c: Callable[[int32], int32]) -> Callable[[int32], int32]:
    await asyncio.sleep(0)
    return c


def dbl(x: int32) -> int32:
    return x * 2


# the owned spelling an erasure boundary needs
async def own_pair(b: Box) -> tuple[int32, Own[Box]]:
    await asyncio.sleep(0)
    return (2, Box(b.v + 1))


class Holder:
    b: Box

    def __init__(self) -> None:
        self.b = Box(10)

    # method: a borrow tuple of a field
    async def get(self) -> tuple[int32, Box]:
        await asyncio.sleep(0)
        return (3, self.b)

    # method: an open T element
    async def wrap[T](self, x: T) -> tuple[int32, T]:
        await asyncio.sleep(0)
        return (4, x)


# try/finally: the return leaves through an inline finally
async def fin_inline(b: Box) -> tuple[int32, Box]:
    try:
        await asyncio.sleep(0)
        return (5, b)
    finally:
        print("fin_inline finally")


# try/finally: the return leaves through a suspending finally
async def fin_suspend[T](x: T) -> tuple[T, int32]:
    try:
        await asyncio.sleep(0)
        return (x, 6)
    finally:
        await asyncio.sleep(0)
        print("fin_suspend finally")


# return await: the same tuple form forwards as it is
async def fwd(b: Box) -> tuple[int32, Box]:
    return await pair(b)


async def fwd_fn(c: Callable[[int32], int32]) -> Callable[[int32], int32]:
    return await mk_fn(c)


# readonly element: an unpack target binds const
async def ro_pair(b: readonly[Box]) -> tuple[int32, readonly[Box]]:
    await asyncio.sleep(0)
    return (1, b)


# generic forward of a task's tuple payload
async def task_fwd[T](x: T) -> tuple[T, int32]:
    tk = asyncio.create_task(solo(x))
    return await tk


# generic forward of a wait_for tuple payload
async def wait_fwd[T](x: T) -> tuple[T, int32]:
    return await asyncio.wait_for(solo(x), 1.0)


# a Literal-str element: the storage tuple payload
async def lit() -> tuple[Literal["abc"], int32]:
    await asyncio.sleep(0)
    return ("abc", 1)


def ro_pair_sync(b: readonly[Box]) -> tuple[int32, readonly[Box]]:
    return (1, b)


# generator: a readonly element unpacked from a sync call, held across a yield
def ro_gen(b: Box) -> Iterator[int32]:
    n, x = ro_pair_sync(b)  # tpyc: ok
    yield n
    b.v = 23
    yield x.v


# a storage tuple beside an open T (an Optional element)
async def opt_solo[T](x: T) -> tuple[T, int32 | None]:
    await asyncio.sleep(0)
    return (x, None)


class Gen:
    i: int32

    def __init__(self) -> None:
        self.i = 0

    def __aiter__(self) -> "Gen":
        return self

    async def __anext__(self) -> tuple[int32, Box]:
        await asyncio.sleep(0)
        if self.i >= 2:
            raise StopAsyncIteration
        self.i += 1
        return (self.i, SHARED)


class Ctx:
    b: Box

    def __init__(self) -> None:
        self.b = Box(20)

    async def __aenter__(self) -> tuple[int32, Box]:
        await asyncio.sleep(0)
        return (7, self.b)

    async def __aexit__(self, et: None, ev: None, tb: None) -> None:
        pass


async def amain() -> None:
    b = Box(1)
    # bind: the element aliases b after a suspension
    t = await pair(b)  # tpyc: ok
    await asyncio.sleep(0)
    t[1].v = 7
    print("bind", t[0], b.v)
    # generic bind at a reference T
    u = await solo(b)  # tpyc: ok
    await asyncio.sleep(0)
    u[0].v = 8
    print("generic bind", u[1], b.v)
    # generic bind at a value T
    w = await solo(3)  # tpyc: ok
    print("generic value", w[0], w[1])
    # Callable payload, called after a suspension
    f = await mk_fn(dbl)  # tpyc: ok
    await asyncio.sleep(0)
    print("callable", f(4))
    # Callable payload through a task
    tk = asyncio.create_task(mk_fn(dbl))  # tpyc: ok
    print("task callable", (await tk)(7))
    # method payloads
    h = Holder()
    m = await h.get()  # tpyc: ok
    await asyncio.sleep(0)
    m[1].v = 11
    print("method", m[0], h.b.v)
    mw = await h.wrap(b)  # tpyc: ok
    mw[1].v = 12
    print("method generic", mw[0], b.v)
    # unpack: the lift holds the payload's pointers
    n, x = await pair(b)  # tpyc: ok
    await asyncio.sleep(0)
    x.v = 13
    print("unpack", n, b.v)
    gx, gn = await solo(b)  # tpyc: ok
    gx.v = 14
    print("generic unpack", gn, b.v)
    # nested: an element of the awaited tuple written in place
    (await pair(b))[1].v = 15  # tpyc: ok
    print("nested", b.v)
    (await solo(b))[0].v = 16  # tpyc: ok
    print("generic nested", b.v)
    # append: the container stores its own copy, as the sync twin does
    xs: list[tuple[int32, Box]] = []
    xs.append(await pair(b))  # tpyc: warning(/copies Box/)
    print("append", len(xs), b.v)
    # try/finally returns
    fi = await fin_inline(b)  # tpyc: ok
    await asyncio.sleep(0)
    fi[1].v = 17
    print("fin_inline", fi[0], b.v)
    fs = await fin_suspend(b)  # tpyc: ok
    await asyncio.sleep(0)
    fs[0].v = 18
    print("fin_suspend", fs[1], b.v)
    # return await of the same form
    r = await fwd(b)  # tpyc: ok
    await asyncio.sleep(0)
    r[1].v = 19
    print("fwd", r[0], b.v)
    rf = await fwd_fn(dbl)  # tpyc: ok
    print("fwd callable", rf(5))
    # readonly unpack: the target points at b, const
    rn, rx = await ro_pair(b)  # tpyc: ok
    b.v = 22
    await asyncio.sleep(0)
    print("readonly unpack", rn, rx.v)
    # generic task forward at a value T
    tf = await task_fwd(3)  # tpyc: ok
    print("task fwd", tf[0], tf[1])
    # generic wait_for forward at a value T
    wf = await wait_fwd(3)  # tpyc: ok
    print("wait fwd", wf[0], wf[1])
    # Literal-str element
    await lit()  # tpyc: ok
    print("lit", "ok")
    # generator readonly unpack
    for g in ro_gen(b):
        print("readonly gen", g)
    # generic storage tuple at a value T
    ot = await opt_solo(5)  # tpyc: ok
    print("opt generic", ot[0], ot[1] is None)
    # a temporary argument sits in this frame, warned falsely as in sync
    # (BUGS.md#lend-back-of-hoisted-temp-warned-as-dangling)
    tu = await solo(Box(99))  # tpyc: warning(/temporary/)
    await asyncio.sleep(0)
    print("temp arg", tu[0].v, tu[1])
    # async for: the loop var holds the element's pointer
    async for it in Gen():  # tpyc: ok
        await asyncio.sleep(0)
        it[1].v += 10
        print("async for", it[0], SHARED.v)
    # async with: the target holds the element's pointer
    c = Ctx()
    async with c as ct:  # tpyc: ok
        await asyncio.sleep(0)
        ct[1].v = 21
    print("async with", ct[0], c.b.v)


def main() -> None:
    asyncio.run(amain())
    # erasure boundaries: an owned or value payload runs under asyncio.run
    b = Box(30)
    o = asyncio.run(own_pair(b))  # tpyc: ok
    o[1].v = 99
    print("run owned", o[0], o[1].v, b.v)
    v = asyncio.run(solo(3))  # tpyc: ok
    print("run generic value", v[0], v[1])


main()
