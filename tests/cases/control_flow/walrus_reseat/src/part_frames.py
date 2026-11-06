# The rebinding walrus in generator and coroutine frames.
import asyncio
from typing import Iterator

from tpy import int32

from fixtures import A, H, OwnCtx, Ring


def gen(h: H) -> Iterator[int32]:
    x = h.a
    # Generator frame: a record field.
    print("gen", (x := h.b).n)  # tpyc: ok
    yield 0
    x.n += 10
    print("gen", h.a.n, h.b.n)
    # An element.
    print("gen", (x := h.xs[1]).n)  # tpyc: ok
    yield 0
    x.n += 10
    print("gen", h.xs[1].n)
    o = h.o
    # An Optional field.
    if (o := h.b) is not None:  # tpyc: ok
        o.n += 100
    yield 0
    print("gen", h.b.n)
    t = (1, 2)
    # A value tuple.
    print("gen", (t := (3, 4))[0])  # tpyc: ok
    yield t[1]
    y = h.a
    # A yielded value, lowered outside the frame's leaves.
    yield (y := h.xs[0]).n  # tpyc: ok
    y.n += 10
    print("gen", h.xs[0].n)


def gen_cond(r: Ring) -> Iterator[int32]:
    o = r.after(r.items[0])
    yield 0
    # A generator's branch condition.
    while o is not None and (o := r.after(o)) is not None:  # tpyc: ok
        o.n += 100
        yield o.n


async def get(a: A) -> int32:
    await asyncio.sleep(0)
    return a.n


def gen_with() -> Iterator[int32]:
    with OwnCtx() as p:
        q = p
        yield 0
        # A generator's `with` target over an owned result, rebound to a
        # fresh object.
        if (p := A(2)).n > 0:  # tpyc: ok
            q.n += 10
        print("gen_with", q.n, p.n)
        yield 1


async def coro(h: H) -> int32:
    x = h.a
    # Coroutine frame: a record field.
    if (x := h.b).n > 0:  # tpyc: ok
        x.n += 10
    await asyncio.sleep(0)
    print("async", h.a.n, h.b.n, x.n)
    o = h.o
    # An Optional field.
    if (o := h.o) is not None:  # tpyc: ok
        o.n += 10
    await asyncio.sleep(0)
    print("async", o is None, h.o.n if h.o is not None else -1)
    # An awaited operand.
    k = await get((x := h.xs[1]))  # tpyc: ok
    x.n += 10
    print("async", k, h.xs[1].n)
    # A returned value.
    return (x := h.xs[0]).n  # tpyc: ok


def main() -> None:
    for v in gen(H()):
        print("gen", "yield", v)
    for _ in gen_with():
        pass
    for v in gen_cond(Ring()):
        print("gen_cond", v)
    r = asyncio.run(coro(H()))
    print("async", r)
