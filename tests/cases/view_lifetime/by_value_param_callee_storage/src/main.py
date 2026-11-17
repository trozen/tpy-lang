# A parameter the function holds its own copy of (a by-value ValueType
# record, `tuple[...] | None`, `Own[...]`, an async frame's copy) is the
# function's storage: a view of it cannot leave the function, so each
# position returns the owned `str` instead. The parts pin what stays a view.
import asyncio
from typing import Callable, Iterator
from tpy import Own, Ptr, int32
from fixtures import V, Sq, P
import part_inverse
import part_frames


def name_of(x: V) -> str:
    # Free function: the owned copy leaves, not a view of `x`.
    return x.name  # tpyc: ok


def first_of(x: tuple[str, int32] | None) -> str:
    # By-value optional tuple parameter.
    if x is not None:
        return x[0]  # tpyc: ok
    return ""


def owned_name(x: Own[V]) -> str:
    return x.get()  # tpyc: ok


class R:
    k: int32

    def __init__(self) -> None:
        self.k = 1

    def name_of(self, x: V) -> str:
        # Method.
        return x.get()  # tpyc: ok


class H:
    v: str

    def __init__(self, x: V) -> None:
        # Constructor: the field owns its copy.
        self.v = x.get()  # tpyc: ok


async def aname(x: V) -> str:
    # Async: the frame's copy of `x`.
    return x.name  # tpyc: ok


async def atext(s: str) -> str:
    # Async: the frame copies a `str` parameter.
    return s  # tpyc: ok


async def atuple(x: tuple[str, int32]) -> str:
    # Async: the frame copies a tuple parameter.
    return x[0]  # tpyc: ok


async def aunion(x: str | int32) -> str:
    # Async: the frame copies a value union parameter.
    if isinstance(x, str):
        return x  # tpyc: ok
    return ""


def gnames(x: V) -> Iterator[str]:
    # Generator.
    yield x.name  # tpyc: ok


def via_lambda(x: V) -> str:
    # Lambda capturing the parameter.
    g: Callable[[], str] = lambda: x.name  # tpyc: ok
    return g()


def pick(x: V) -> Own[V | Sq]:
    # Union with a reference alternative: returned by value.
    return x  # tpyc: ok


async def abump(p: Ptr[P]) -> int32:
    # The same pointer reaches the caller's object.
    p.n += 1
    return p.n  # tpyc: ok


async def amain() -> None:
    print("async:", await aname(V("a" * 20)), await atext("t" * 20))
    print("async copies:", await atuple(("p" * 20, 1)), await aunion("u" * 20))
    q = P(3)
    n = await abump(q)
    # The write through the frame's pointer is seen in the caller's `q`.
    print("async ptr:", n, q.n)


def main() -> None:
    print("free:", name_of(V("f" * 20)), first_of(("o" * 20, 1)),
          owned_name(V("w" * 20)))
    r = R()
    print("method:", r.name_of(V("m" * 20)))
    h = H(V("c" * 20))
    print("ctor:", h.v)
    asyncio.run(amain())
    print("generator:", list(gnames(V("g" * 20))))
    print("lambda:", via_lambda(V("l" * 20)))
    u = pick(V("u" * 20))
    if isinstance(u, V):
        print("union:", u.name)
    part_inverse.main()
    part_frames.main()


main()
