# A coroutine method whose receiver is a temporary, or read out of one: the
# frame holds the owner while the coroutine runs. The awaited receiver is
# handed to the coroutine as the object itself, whatever form the receiver
# expression has (a pointer, a select, an element).
import asyncio

from tpy import Own, Ptr, int32, nocopy


@nocopy
class Summer:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    async def add(self, x: int32) -> int32:
        await asyncio.sleep(0.0)
        # A write through the receiver: the frame must hold the owner, not
        # a copy, for the next read to see it.
        self.base += 1
        junk = [Summer(999) for _ in range(4)]
        return self.base + x + len(junk) - 4


@nocopy
class Pair:
    left: Summer
    right: Summer

    def __init__(self, left: Own[Summer], right: Own[Summer]) -> None:
        self.left = left
        self.right = right


def make_pair(a: int32, b: int32) -> Own[Pair]:
    return Pair(Summer(a), Summer(b))


async def outer() -> int32:
    t = asyncio.create_task(make_pair(100, 200).left.add(1))  # tpyc: ok
    await asyncio.sleep(0.0)
    return await t


async def awaited() -> int32:
    # Awaited in place: the owner lives for the statement, across the await.
    t = 0
    for i in range(3):
        t += await make_pair(i, i * 10).right.add(i)  # tpyc: ok
    r = await Summer(5).add(1)  # tpyc: ok
    return t * 100 + r


def mk(n: int32) -> Own[Summer]:
    return Summer(n)


async def pointer_forms(xs: list[Summer], p: Ptr[Summer],
                        o: Summer | None) -> None:
    out: list[int32] = []
    # A loop target that borrows each element.
    for s in xs:
        out.append(await s.add(1))  # tpyc: ok
    # A `Ptr` receiver, unchecked and then proven non-null.
    out.append(await p.add(2))  # tpyc: ok
    if p is not None:
        out.append(await p.add(3))  # tpyc: ok
    # A narrowed optional receiver.
    if o is not None:
        out.append(await o.add(4))  # tpyc: ok
    print("receiver_pointer", out, xs[0].base, xs[1].base, p.base)


async def select_recv(a: Summer, c: bool) -> None:
    # A select of a named and a fresh receiver: the fresh one is held by the
    # frame across the await, the named one is the object itself.
    out = [await (a if c else mk(5)).add(1)]  # tpyc: ok
    for i in range(4):
        out.append(await (a if i % 2 == 0 else mk(i * 10)).add(1))  # tpyc: ok
    print("receiver_select", c, out, a.base)


async def alias_recv(xs: list[Summer], d: dict[str, Summer]) -> None:
    # An element receiver: the write shows in the container afterwards.
    r = await xs[1].add(5)  # tpyc: ok
    q = await d["k"].add(6)  # tpyc: ok
    print("receiver_alias", r, xs[1].base, q, d["k"].base)


async def bound_temp() -> None:
    # A coroutine bound off a temporary receiver, awaited in the next
    # statement: the frame holds the receiver for the binding's statement,
    # right only while nothing refills that holder
    # (BUGS.md#frame-handle-outlives-fresh-argument).
    c = Summer(40).add(5)  # tpyc: ok
    print("receiver_bound", await c)


def main() -> None:
    print("receiver", asyncio.run(outer()))
    print("receiver_awaited", asyncio.run(awaited()))
    xs = [Summer(10), Summer(20)]
    t = Summer(30)
    asyncio.run(pointer_forms(xs, t, Summer(50)))
    a = Summer(100)
    asyncio.run(select_recv(a, False))
    asyncio.run(select_recv(a, True))
    d: dict[str, Summer] = {}
    d["k"] = Summer(70)
    asyncio.run(alias_recv(xs, d))
    asyncio.run(bound_temp())
