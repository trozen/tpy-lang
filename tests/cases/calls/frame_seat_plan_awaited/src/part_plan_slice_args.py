# A slice handed to a generator or coroutine call that dangles nothing: a
# parameter that owns its argument copies a slice of a fresh value, a view a
# call returns over a live local is no storage of its own, and an index is
# consumed where it is read.
import asyncio
from typing import Iterable, Iterator
from tpy import StrView, int32


def make(n: int32) -> str:
    return "abcdefghijklmnop" + str(n)


def view(s: str) -> StrView:
    return s


def pick() -> int32:
    return 0


async def co_str(s: str) -> str:
    await asyncio.sleep(0)
    return s


def gen_s(s: str) -> Iterator[str]:
    yield s
    yield s


async def collect[T](it: Iterable[T]) -> str:
    out = ""
    await asyncio.sleep(0)
    for x in it:
        out += str(x)
    return out


def gen_owned(n: int32) -> Iterator[str]:
    # generator head: the owning `str` parameter copies the fresh slice
    for s in gen_s(make(n)[1:5]):  # tpyc: ok
        yield s


async def awaited(n: int32) -> None:
    pads = ["Q" * 40, "R" * 40]
    # awaited operand: the owning `str` parameter copies the fresh slice
    print("slice owned param", await co_str(make(n)[1:5]), pads[0][0])  # tpyc: ok
    s = "  abcdefghijklmnopqrstuvwxyz0123456789  " * 2
    # a slice of a view a call returns over the live local `s`
    print("slice of view", await collect(view(s)[1:10]), pads[1][0])  # tpyc: ok
    xs = ["abcdefghijklmnopqrstuvwxyz0123456789" * 2]
    # a slice of an element of a live list; the index call is consumed
    print("slice index call", await collect(xs[pick()][1:10]), pads[0][1])  # tpyc: ok
    # the views stay live across the awaits: the local is rebound after
    s = "z"
    xs.append(s)


def main() -> None:
    out = ""
    for s in gen_owned(7):
        out += s + "|"
    print("slice gen owned param", out)
    asyncio.run(awaited(7))
