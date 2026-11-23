# Where a frame seat lives: a generator handle over a generator handle, a
# statement that never suspends, tasks over fresh arguments and receivers.
import asyncio
from typing import Iterable, Iterator, Protocol
from tpy import Own, StrView, dynamic, int32


class Item:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Src:
    items: list[Item]

    def __init__(self, n: int32) -> None:
        print("frames make", n)
        self.items = [Item(n), Item(n + 1)]

    async def total(self) -> int32:
        await asyncio.sleep(0)
        return self.items[0].v + self.items[1].v


def walk(s: Src) -> Iterator[Item]:
    for it in s.items:
        yield it


def inner(it: Iterator[Item]) -> Iterator[int32]:
    for x in it:
        x.v += 7
        yield x.v


def nested() -> Iterator[int32]:
    # a generic generator keeps the handle it is given; both live on the frame
    for z in inner(walk(Src(3))):  # tpyc: ok
        yield z


def first(it: Iterator[Item]) -> int32:
    for x in it:
        # BUGS.md#ephemeral-value-read-escape: `return x.v` is refused
        n = x.v
        return n
    return -1


def per_pass(k: int32) -> Iterator[int32]:
    i = 0
    # neither statement suspends while its temporaries live: statement temps
    while first(walk(Src(i))) < k:  # tpyc: ok
        yield sum([x.v for x in walk(Src(i + 10))])  # tpyc: ok
        i += 1


async def work(s: Src) -> int32:
    await asyncio.sleep(0)
    return s.items[1].v


async def tasks(a: int32) -> None:
    # a task keeps its fresh argument and its fresh receiver past the statement
    t = asyncio.create_task(work(Src(5)))  # tpyc: ok
    h = asyncio.create_task(Src(7).total())  # tpyc: ok
    junk = [[j] * 20 for j in range(50)]
    await asyncio.sleep(0)
    # a conditional of two fresh arguments of an awaited call
    c = await work(Src(1) if a > 3 else Src(2))  # tpyc: ok
    print("frames tasks", await t, await h, c, len(junk))


async def consume(it: Iterable[int32]) -> int32:
    n = 0
    for x in it:
        await asyncio.sleep(0)
        n += x
    return n


async def literal() -> None:
    # a literal at a protocol parameter goes to the coroutine that owns it
    print("frames literal", await consume([1, 2, 3]))  # tpyc: ok


@dynamic
class Has(Protocol):
    def first(self) -> int32: ...


class Bag:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n, n + 1]

    def first(self) -> int32:
        return self.xs[0]


def pair(h: Has) -> Own[list[int32]]:
    return [h.first(), h.first() + 1]


def adapted() -> Iterator[int32]:
    # a @dynamic adapter built for the head lives on the frame
    for x in pair(Bag(4)):  # tpyc: ok
        yield x


def chars(s: StrView) -> Iterator[str]:
    for c in s:
        yield str(c)


def mks(n: int32) -> str:
    return "abcdefghijklmnopqrstuvwxyz0123456789-" + str(n)


def sp(s: str) -> StrView:
    return s


class P:
    tag: str

    def __init__(self, x: int32) -> None:
        self.tag = "tag-abcdefghijklmnopqrstuvwxyz-" + str(x)

    def name(self) -> StrView:
        return self.tag


async def a_for() -> str:
    out = ""
    # a generator handle over a view of a fresh value, across an `await`
    for y in chars(sp(mks(3))):  # tpyc: ok
        await asyncio.sleep(0)
        out += y
    # ... and over a view a fresh receiver lends
    for y in chars(P(2).name()):  # tpyc: ok
        await asyncio.sleep(0)
        out += y
    return out


def main() -> None:
    ns = list(nested())
    print("frames nested", ns)
    ps = list(per_pass(2))
    print("frames per_pass", ps)
    asyncio.run(tasks(5))
    asyncio.run(literal())
    print("frames adapted", list(adapted()))
    print("frames a_for", asyncio.run(a_for()))
