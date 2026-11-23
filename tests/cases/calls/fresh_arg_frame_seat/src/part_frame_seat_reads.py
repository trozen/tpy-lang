# A loop's awaited view of a fresh argument, read by an operator, a
# subscript, a conversion or a call that keeps nothing of it: each pass's
# argument dies with its statement, so the loop shares no seat.
import asyncio
from typing import Iterator, Self
from tpy import StrView, int32


class Src:
    xs: list[int32]
    text: str

    def __init__(self, n: int32) -> None:
        self.xs = [n, n + 1]
        self.text = str(n) * 40


def gen(s: Src) -> Iterator[int32]:
    for x in s.xs:
        yield x


async def view(s: Src) -> StrView:
    await asyncio.sleep(0)
    return s.text


async def aval(n: int32) -> int32:
    await asyncio.sleep(0)
    return n


async def solo(x: int32) -> int32:
    await asyncio.sleep(0)
    return x


async def reads() -> None:
    for i in range(3):
        # each read hands on a value that keeps nothing of the view
        print("reads cat " + (await view(Src(i)))[:3])  # tpyc: ok
        print("reads in", "1" in (await view(Src(i))))  # tpyc: ok
        print("reads not", not (await view(Src(i))))  # tpyc: ok
        print("reads slice", (await view(Src(i)))[1:3])  # tpyc: ok
        print("reads str", str(await view(Src(i)))[:2])  # tpyc: ok
        print("reads eq", (await view(Src(i))) == str(i) * 40)  # tpyc: ok


async def generators() -> None:
    for i in range(3):
        # a generator expression's source, consumed by `sum` / `list`
        print("reads sum", sum(x for x in gen(Src(i))), await aval(0))  # tpyc: ok
        await aval(0)
        print("reads list", list(gen(Src(i))))  # tpyc: ok


async def stored() -> None:
    out: list[str] = []
    for i in range(3):
        # the slice is copied into an owned `str` the list stores
        out.append((await view(Src(i)))[:3])  # tpyc: ok
        # an await inside an f-string, over a builtin reading a generator
        print("reads fstr", f"{await solo(max(gen(Src(i + 5))))}")  # tpyc: ok
    print("reads stored", out)


async def literal_source() -> None:
    ss: list[Src] = []
    for i in range(3):
        # a list literal's fresh element moves into the loop variable
        for v in [Src(i * 10)]:  # tpyc: ok
            ss.append(v)
        await aval(0)
    print("reads literal", ss[2].xs[0])


class CM:
    n: int32

    def __init__(self, s: StrView) -> None:
        self.n = len(s)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, a, b, c) -> None:
        pass


async def other_positions() -> None:
    lo = "/"
    hi = "z"
    for i in range(3):
        # a chained comparison's first operand, and a `with` manager's
        # argument, each a lifted await read where it is consumed (named
        # strings: a pair of literals compares addresses,
        # BUGS.md#str-literal-pair-compares-pointers)
        print("reads chained", (await view(Src(i))) < hi > lo)  # tpyc: ok
        with CM(await view(Src(i))) as c:  # tpyc: ok
            print("reads with", c.n)


def main() -> None:
    asyncio.run(other_positions())
    asyncio.run(reads())
    asyncio.run(generators())
    asyncio.run(stored())
    asyncio.run(literal_source())
