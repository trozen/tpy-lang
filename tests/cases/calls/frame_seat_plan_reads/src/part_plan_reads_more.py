# A coerced argument finds its plan entry; `in` lowers through the call
# sema built; a select of literals and a value consumed on the spot are
# no frame holders; a fresh `*args` element a frame keeps is held; an
# awaited call's direct operand -- of a function or a method -- is captured
# by the awaited frame as it is.
import asyncio
from typing import Iterable, Iterator
from tpy import Own, Span, StrView, char, int32, readonly


def mks(n: int32) -> str:
    return f"abcdefghijklmnopqrstuvwxyz0123456789-{n}"


def id_view(v: StrView) -> StrView:
    return v


def id_span(s: Span[readonly[int32]]) -> Span[readonly[int32]]:
    return s


def gen_view(v: StrView) -> Iterator[str]:
    yield str(v)


def gen_span(s: Span[readonly[int32]]) -> Iterator[int32]:
    yield s[0] + s[1] + s[2]


def drain_view(it: Iterator[str]) -> str:
    out = ""
    for s in it:
        out = s
    return out


def drain_span(it: Iterator[int32]) -> int32:
    acc = 0
    for x in it:
        acc += x
    return acc


def coerced() -> None:
    # a temporary under a conversion, nested in a frame argument
    print("more coerced", drain_view(gen_view(id_view(mks(2)))))  # tpyc: ok
    print("more span", drain_span(gen_span(id_span([10, 20, 30]))))  # tpyc: ok


class Bag:
    def __init__(self, xs: Own[list[str]]) -> None:
        self.xs = xs

    def __contains__(self, s: str) -> bool:
        return s in self.xs


async def membership() -> int32:
    hits = 0
    for i in range(3):
        # `x in Bag(...)` across a suspension of the loop
        if str(i) in Bag(["0", "2"]):  # tpyc: ok
            hits += 1
        await asyncio.sleep(0)
    return hits


async def asv(s: StrView) -> int32:
    await asyncio.sleep(0)
    return len(s)


async def ai(n: int32) -> int32:
    await asyncio.sleep(0)
    return n


async def consumed() -> None:
    # an argument consumed inside an awaited operand, and a select of
    # literals at a view parameter: neither is held on the frame
    print("more consumed", await ai(len(mks(3))))  # tpyc: ok
    print("more literal select",
          await asv("a fairly long literal beyond sso size!!"
                    if len(mks(1)) > 0 else "r"))  # tpyc: ok


class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def pick_first(*xs: Src) -> Src:
    return xs[0]


def gen_src(s: Src) -> Iterator[int32]:
    yield s.n
    yield s.n + 1


async def vararg_seat() -> int32:
    acc = 0
    # a fresh element the `*args` pack points at, kept across the loop's
    # suspensions
    for v in gen_src(pick_first(Src(10), Src(20))):  # tpyc: ok
        await asyncio.sleep(0)
        acc += v
    return acc


G = "global_text_long_enough_for_the_heap_0123"


async def collect(xs: Iterable[char]) -> str:
    await asyncio.sleep(0)
    out = ""
    for c in xs:
        out += str(c)
    return out


class Holder:
    def __init__(self, text: str) -> None:
        self.text = text


async def awaited_operands(h: Holder, xs: list[str], s: str, t: str,
                           n: int32) -> None:
    # a field, an element, a parameter, a global and a local, alone or in
    # a select beside a fresh value, as an awaited call's direct operand
    print("more await field", await collect(h.text))  # tpyc: ok
    print("more await field select",
          await collect(h.text if n > 0 else f"fresh_{n}"))  # tpyc: ok
    print("more await element select",
          await collect(xs[0] if n > 0 else f"fresh_{n}"))  # tpyc: ok
    print("more await element or", await collect(xs[0] or f"f_{n}"))  # tpyc: ok
    print("more await param or", await collect(s or t))  # tpyc: ok
    print("more await global select",
          await collect(G if n > 0 else f"fresh_{n}"))  # tpyc: ok
    a = f"local_abcdefghijklmnopqrstuvwxyz_{n}"
    b = f"other_abcdefghijklmnopqrstuvwxyz_{n}"
    print("more await local or", await collect(a or b))  # tpyc: ok
    print("more await local and", await collect(a and b))  # tpyc: ok
    # a fresh f-string beside a parameter the frame holds owned
    print("more await param select",
          await collect(f"fresh_abcdefghijklmnopqrstuvwxyz_{n}"
                        if n > 1 else t))  # tpyc: ok


class C:
    def __init__(self) -> None:
        pass

    async def count(self, xs: Iterable[char]) -> int32:
        await asyncio.sleep(0)
        m = 0
        for _c in xs:
            m += 1
        return m


async def method_operands(c: C, h: Holder, xs: list[str], s: str, t: str,
                          n: int32) -> None:
    # ... through a coroutine method
    print("more method field", await c.count(h.text))  # tpyc: ok
    print("more method element or", await c.count(xs[0] or f"f_{n}"))  # tpyc: ok
    print("more method param or", await c.count(s or t))  # tpyc: ok
    print("more method global select",
          await c.count(G if n > 5 else f"fresh_{n}"))  # tpyc: ok


def main() -> None:
    coerced()
    print("more membership", asyncio.run(membership()))
    asyncio.run(consumed())
    print("more vararg", asyncio.run(vararg_seat()))
    asyncio.run(awaited_operands(
        Holder("field_abcdefghijklmnopqrstuvwxyz"),
        ["element_abcdefghijklmnopqrstuvwxyz"], "",
        "second_abcdefghijklmnopqrstuvwxyz", 1))
    # ... picking the fresh arm of each select
    asyncio.run(awaited_operands(
        Holder("field_abcdefghijklmnopqrstuvwxyz"), [""], "",
        "second_abcdefghijklmnopqrstuvwxyz", 0))
    asyncio.run(method_operands(
        C(), Holder("field_abcdefghijklmnopqrstuvwxyz"), [""], "",
        "second_abcdefghijklmnopqrstuvwxyz", 1))
    asyncio.run(method_operands(
        C(), Holder("field_abcdefghijklmnopqrstuvwxyz"),
        ["element_abcdefghijklmnopqrstuvwxyz"], "",
        "second_abcdefghijklmnopqrstuvwxyz", 9))
