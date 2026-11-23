# A slice of a fresh value handed to an awaited coroutine: the frame holds the
# sliced-from value across the `await`, so the awaited frame's view stays live.
import asyncio
from typing import Iterable, Iterator

from tpy import BytesView, Own, StrView, int32


def make(n: int32) -> str:
    return "abcdefghijklmnopqrstuvwxyz0123456789" * n


def make_bytes(n: int32) -> bytes:
    return b"0123456789abcdef" * n


def make_list(n: int32) -> Own[list[int32]]:
    out: list[int32] = []
    for i in range(n):
        out.append(i * 10)
    return out


async def collect[T](it: Iterable[T]) -> str:
    await asyncio.sleep(0)
    # Fresh allocations between the suspension and the read reuse any
    # storage the argument had freed.
    junk = [make(3) for _ in range(6)]
    out = ""
    for c in it:
        out += str(c)
    return out + "/" + str(len(junk))


async def co_str(s: str) -> str:
    await asyncio.sleep(0)
    return s


async def body(n: int32) -> None:
    print("slice", await collect(make(n)[2:12]))  # tpyc: ok
    # A slice under a further view: the frame holds the sliced-from string,
    # the views over it are values.
    print("slice_strip", await collect(f"  {n}abc  "[1:].strip()))  # tpyc: ok
    print("slice_bytes", await collect(make_bytes(n)[2:6]))  # tpyc: ok
    print("slice_list", await collect(make_list(n + 3)[1:3]))  # tpyc: ok
    # A `str` parameter owns its argument: the slice is copied, no holder.
    print("slice_owned", await co_str(make(n)[1:5]))  # tpyc: ok
    # A slice of a `str` literal views static storage: no holder.
    print("slice_literal", await collect("hello world"[2:7]))  # tpyc: ok
    # ... and so does a slice of a `bytes` literal.
    print("slice_bytes_literal", await collect(b"hello world"[2:7]))  # tpyc: ok
    # A `bytes` literal a view method reads: its static storage, no holder.
    print("strip_bytes_literal",
          await collect(b"  hello-bytes-literal  ".strip()))  # tpyc: ok


def views(s: StrView) -> Iterator[StrView]:
    yield s


def bviews(b: BytesView) -> Iterator[BytesView]:
    yield b


def sync_head() -> None:
    # An ordinary function's loop head keeps the string its slice views
    # until the loop ends.
    out: list[str] = []
    for i in range(2):
        for w in views(make(i + 1)[i + 1:].strip()):  # tpyc: ok
            out.append(str(w[0:3]))
    print("slice_sync_head", out)
    for b in bviews(b"  hello-bytes-literal  ".strip()):  # tpyc: ok
        junk = [make(3) for _ in range(6)]
        print("strip_bytes_sync_head", b, len(junk))


def main() -> None:
    asyncio.run(body(2))
    sync_head()
