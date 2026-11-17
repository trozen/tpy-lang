# Parameters that are the caller's storage, or views of it: a view of them
# still leaves the function.
import asyncio
from typing import Callable, Iterable, Protocol
from tpy import StrView, BytesView, Span, Own, Ptr, int32
from fixtures import V, P


def opt_str(x: str | None) -> StrView:
    if x is not None:
        return x  # tpyc: ok
    return ""


def opt_view(x: StrView | None) -> StrView:
    if x is not None:
        return x[1:]  # tpyc: ok
    return ""


def opt_bytes(x: BytesView | None) -> BytesView:
    if x is not None:
        return x  # tpyc: ok
    return b""


def span(x: Span[int32]) -> Span[int32]:
    return x  # tpyc: ok


def view(x: StrView) -> StrView:
    return x  # tpyc: ok


def text(x: str) -> StrView:
    return x  # tpyc: ok


def tup(x: tuple[str, int32]) -> StrView:
    # A tuple parameter is the caller's tuple.
    return x[0]  # tpyc: ok


def union(x: str | int32) -> StrView:
    # A value union parameter is the caller's union.
    if isinstance(x, str):
        return x  # tpyc: ok
    return ""


def opt_int(x: int32 | None) -> int32:
    return x if x is not None else 0  # tpyc: ok


class C:
    def __init__(self, cb: Callable[[], int32]) -> None:
        self.cb = cb  # tpyc: ok


def one() -> int32:
    return 1


def total(xs: Iterable[Own[P]]) -> int32:
    # An owning element parameter owns each element, not the iterable.
    t = 0
    for p in xs:
        t += p.n
    return t


class Named(Protocol):
    def get(self) -> StrView: ...


def gname[T: Named](x: T) -> StrView:
    # A generic parameter is the caller's object at every instantiation.
    return x.get()  # tpyc: ok


async def aptr_name(p: Ptr[V]) -> StrView:
    # Async: the frame copies the pointer, not the caller's `V` it points at.
    return p.name  # tpyc: ok


async def aopt_name(p: Ptr[V] | None) -> StrView:  # tpyc: warning(/`Ptr\[V\] \| None` is redundant/)
    # Async, nullable pointer: the view outlives a suspension.
    if p is None:
        return ""
    await asyncio.sleep(0)
    return p.name  # tpyc: ok


async def amain() -> None:
    v = V("r" * 20)
    print("inverse async ptr:", (await aptr_name(v))[:2],
          (await aopt_name(v))[:2], await aopt_name(None) == "")


def main() -> None:
    s = "s" * 20
    b = b"b" * 20
    xs: list[int32] = [1, 2]
    print("inverse optional:", opt_str(s)[:2], opt_view(s)[:2],
          len(opt_bytes(b)))
    print("inverse views:", len(span(xs)), view(s)[:2], text(s)[:2])
    t = ("t" * 20, 1)
    u: str | int32 = "u" * 20
    print("inverse tuple union:", tup(t)[:2], union(u)[:2], opt_int(3))
    # A by-value receiver is the caller's object (`V.get`).
    y = V("y" * 20)
    print("inverse self:", y.get())
    c = C(one)
    print("inverse callable:", c.cb())
    print("inverse elements:", total([P(1), P(2)]))
    v = V("n" * 20)
    print("inverse generic:", gname(v)[:2])
    asyncio.run(amain())
