# A statement that only reads a value kept past an await -- a match guard,
# an assert, a loop else, an unpacked loop target, a tuple member that
# carries no borrow, a store's target -- keeps nothing past the statement:
# none of these is refused.
import asyncio
from asyncio import Task
from typing import Iterator
from tpy import Ptr, StrView, int32, readonly


class Src:
    text: str
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.text = str(n) * 40
        self.xs = [n, n + 1]


class Keeps:
    p: Ptr[readonly[Src]]

    def __init__(self, s: readonly[Src]) -> None:
        self.p = s


class Box:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    box: Box

    def __init__(self, n: int32) -> None:
        self.box = Box(n)


async def get_holder(h: Holder) -> Holder:
    await asyncio.sleep(0)
    return h


async def co_n(n: int32) -> int32:
    await asyncio.sleep(0)
    return n


async def view(s: Src) -> StrView:
    await asyncio.sleep(0)
    return s.text


async def co(s: Src) -> int32:
    await asyncio.sleep(0)
    return s.xs[0] + s.xs[1]


def gen_tup(s: Src) -> Iterator[tuple[int32, Src]]:
    yield (s.xs[0], s)


async def guard() -> None:
    for i in range(3):
        v = await view(Src(i))
        match i:
            # the guard reads the view inside the statement
            case n if v:  # tpyc: ok
                print("statements guard", n, v[:2])
            case _:
                pass


async def asserted() -> None:
    for i in range(3):
        # the assert consumes the awaited view
        assert await view(Src(i))  # tpyc: ok
        v = await view(Src(i))
        assert v
        print("statements assert", i, v[:2])


async def while_else() -> int32:
    ts: list[Task[int32]] = []
    i = 0
    while i < 2:
        i += 1
    else:
        # the else runs once: its temporary is no shared loop seat
        ts.append(asyncio.create_task(co(Src(5))))  # tpyc: ok
    t = ts[0]
    return await t


async def unpacked() -> None:
    last_k = 0
    for i in range(3):
        # the unpacked target is read only inside the pass
        for k, v in gen_tup(Src(i * 10)):  # tpyc: ok
            last_k = k
            print("statements unpack", v.xs[1])
        await asyncio.sleep(0)
    print("statements last_k", last_k)


async def members() -> int32:
    ts: list[Task[int32]] = []
    acc = 0
    for i in range(3):
        # only the int member escapes; the member keeping the temporary
        # stays in the pass
        for k, w in [(i * 10, Keeps(Src(i)))]:  # tpyc: ok
            acc += w.p.xs[0]
            ts.append(asyncio.create_task(co_n(k)))
        await asyncio.sleep(0)
    for tk in ts:
        acc += await tk
    return acc


async def stores() -> int32:
    d: dict[str, int32] = {}
    total = 0
    for i in range(3):
        # a subscript and a field target are written, not handed on
        d[str(await view(Src(i)))] = i  # tpyc: ok
        h = await get_holder(Holder(i))  # tpyc: ok
        h.box = Box(i * 10)
        total += h.box.v
    return total + len(d)


def main() -> None:
    asyncio.run(guard())
    asyncio.run(asserted())
    print("statements while else", asyncio.run(while_else()))
    asyncio.run(unpacked())
    print("statements members", asyncio.run(members()))
    print("statements stores", asyncio.run(stores()))
