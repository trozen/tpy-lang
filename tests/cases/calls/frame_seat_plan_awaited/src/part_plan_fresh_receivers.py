# A method call on a fresh receiver handed to a generator or coroutine
# call: the frame holds the result, and every fresh value it borrows (the
# f-string `.strip()` views), at a generator head, an awaited operand and a
# task's coroutine; and a static literal's slice at an awaited operand.
import asyncio
from typing import Iterable, Iterator
from tpy import StrView, int32


def each_t[T](it: Iterable[T]) -> Iterator[T]:
    for x in it:
        yield x


async def collect[T](it: Iterable[T]) -> str:
    out = ""
    await asyncio.sleep(0)
    for x in it:
        out += str(x) + "|"
    return out


class W:
    def __init__(self, s: str) -> None:
        self.s = s

    def view(self) -> StrView:
        return self.s[1:]


def gen_split(n: int32) -> Iterator[str]:
    # generator head: an owned list a fresh f-string's method returns
    for c in each_t(f"{n}abcdefghijklmnopqrstuvwxyz0123".split("a")):  # tpyc: ok
        yield str(c)


def gen_strip(n: int32) -> Iterator[str]:
    # ... a view of the fresh f-string (`.strip()`)
    for c in each_t(f"  {n}abcdefghij  ".strip()):  # tpyc: ok
        yield str(c)


def gen_user_view(n: int32) -> Iterator[str]:
    # ... a user method returning a view of its fresh receiver
    for c in each_t(W(f"{n}abcdefghij").view()):  # tpyc: ok
        yield str(c)


async def awaited(n: int32) -> None:
    pads = ["Q" * 40, "R" * 40]
    # awaited operand: the same shapes
    print("recv awaited split", await collect(f"{n}abcdefghij".split("a")), pads[0][0])  # tpyc: ok
    print("recv awaited strip", await collect(f"  {n}abcdefghij  ".strip()), pads[1][0])  # tpyc: ok
    # a slice of a static literal: the awaited frame captures the slice
    # itself, no holder copies the literal
    print("recv awaited literal slice", await collect("abcdefghijklmnopqrstuvwxyz0123456789"[2:12]), pads[1][1])  # tpyc: ok
    # a task's coroutine keeps an owned method result
    t = asyncio.create_task(collect(f"{n}abcdefghij".split("e")))  # tpyc: ok
    print("recv task split", await t, pads[0][2])


def joined(it: Iterator[str]) -> str:
    pads: list[str] = []
    out = ""
    for c in it:
        pads.append("Z" * 64)
        out += c + "|"
    return out


def main() -> None:
    print("recv gen split", joined(gen_split(7)))
    print("recv gen strip", joined(gen_strip(7)))
    print("recv gen user view", joined(gen_user_view(7)))
    asyncio.run(awaited(7))
