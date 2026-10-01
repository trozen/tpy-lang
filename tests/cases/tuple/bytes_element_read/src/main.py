# A `bytes` tuple element read (`t[0]`) compiles at every position, as the
# `str` twin does. Bound to a local it is an OWNED copy (`::tpy::Bytes y =
# std::get<0>(t);`), so mutating the tuple's source after the bind cannot reach it.
import asyncio
from typing import Iterator
from tpy import int32, Own


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    f: tuple[bytes, int32]

    def __init__(self, f: tuple[bytes, int32]) -> None:
        self.f = f

    def first(self) -> bytes:
        # method: a field tuple element read returned from a method.
        return self.f[0]  # tpyc: ok


def mk(n: int32) -> tuple[bytes, int32]:
    return (str(n).encode() * 2, n)


def mkb() -> tuple[Own[Rec], bytes]:
    return (Rec(1), b"b")


def show(tag: str, b: bytes) -> None:
    print(tag, b, len(b))


def from_param(t: tuple[bytes, int32]) -> bytes:
    print("param", t[0])  # tpyc: ok
    print("param len", len(t[0]))  # tpyc: ok
    print("param eq", t[0] == b"ab")  # tpyc: ok
    show("param arg", t[0])  # tpyc: ok
    y = t[0]  # tpyc: ok
    print("param local", y)
    return t[0]  # tpyc: ok


def gen(t: tuple[bytes, int32]) -> Iterator[bytes]:
    # generator: a bound element and a direct element read across yields.
    y = t[0]  # tpyc: ok
    yield y
    yield t[0]  # tpyc: ok


async def body(t: tuple[bytes, int32]) -> int32:
    # async: the element bound before a suspension is read after it.
    y = t[0]  # tpyc: ok
    await asyncio.sleep(0)
    print("async", y, t[0])  # tpyc: ok
    return len(t[0])  # tpyc: ok


G: tuple[bytes, int32] = (b"glob", 1)

# module-level statement: unpack of the bytes tuple global.
gx, gk = G  # tpyc: ok


def read_global() -> None:
    # the module-level unpack targets read from a function, and the same
    # global unpacked into locals.
    print("global unpack", gx, gk, len(gx))
    x, k = G  # tpyc: ok
    print("global unpack local", x, k)


def main() -> None:
    r = from_param((b"ab", 1))
    print("param ret", r)

    # literal local, then a rebind of the tuple after the element was bound.
    t = (b"lit", 2)
    y = t[0]  # tpyc: ok
    t = (b"new", 3)
    print("literal", y, t[0])  # tpyc: ok

    # call-bound local.
    c = mk(4)
    print("call", c[0], len(c[0]), c[0] == b"44")  # tpyc: ok

    # field: read through a record holding the tuple.
    h = Holder(mk(5))
    print("field", h.f[0])  # tpyc: ok
    print("method", h.first())

    # container element: the copy outlives the container being cleared.
    xs: list[tuple[bytes, int32]] = []
    xs.append(mk(6))
    xs.append(mk(7))
    e = xs[0][0]  # tpyc: ok
    xs.clear()
    print("elem", e, len(xs))

    # for-loop var: the copy outlives the list being cleared.
    ys: list[tuple[bytes, int32]] = []
    ys.append(mk(8))
    ys.append(mk(9))
    keep = b""
    for p in ys:
        keep = p[0]  # tpyc: ok
    ys.clear()
    print("forvar", keep, len(ys))

    # global.
    print("global", G[0], len(G[0]))  # tpyc: ok
    read_global()

    for b in gen(mk(3)):
        print("gen", b)

    n = asyncio.run(body((b"zz", 1)))
    print("async ret", n)

    # comprehension.
    zs: list[tuple[bytes, int32]] = []
    zs.append(mk(1))
    zs.append(mk(2))
    print("comp", [q[0] for q in zs])  # tpyc: ok

    # closure.
    ct = mk(7)

    def clen() -> int:
        return len(ct[0])  # tpyc: ok

    print("closure", clen())

    # match arm.
    match ct[1]:
        case 7:
            print("match", ct[0])  # tpyc: ok
        case _:
            pass

    # try/finally.
    try:
        print("try", ct[0])  # tpyc: ok
    finally:
        print("finally", ct[0])  # tpyc: ok

    # unpack of an Own[record] + bytes call result; the record is mutated after.
    q, bb = mkb()  # tpyc: ok
    q.n = 5
    print("unpack", q.n, bb)


main()
