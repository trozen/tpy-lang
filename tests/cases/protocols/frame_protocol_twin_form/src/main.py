# A generator / async parameter of a structural protocol holds each argument
# as its concrete twin's frame would: a value, a str or bytes, a tuple or a
# field read is copied when the frame is built (a later rebind of the
# caller's storage does not reach it); a record, a container, or the record
# behind an Optional, a union or a tuple element stays shared.
import asyncio
from enum import Enum
from typing import Iterator, Protocol, Sized
from tpy import Hashable, Own, Span, Stringable, ValueType, char, int32


class R:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __repr__(self) -> str:
        return f"R({self.n})"


class P(ValueType):
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def __repr__(self) -> str:
        return f"P({self.x})"


class H:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Box:
    r: R

    def __init__(self, n: int32) -> None:
        self.r = R(n)


def get_r(b: Box) -> R:
    return b.r


class Text(Stringable, Protocol):
    pass


class Color(Enum):
    RED = 1
    GREEN = 2


def make_str() -> str:
    return "made"


def maybe_r(n: int32) -> Own[R | None]:
    if n > 0:
        return R(n)
    return None


def pick(n: int32) -> Own[R | int32]:
    if n > 0:
        return R(n)
    return n


def gen_text(v: Stringable) -> Iterator[str]:
    yield str(v)


def gen_twice(v: Stringable) -> Iterator[str]:
    yield "first"
    yield str(v)


def gen_sub(v: Text) -> Iterator[str]:
    yield str(v)


def gen_hash(v: Hashable) -> Iterator[int]:
    yield hash(v)


def gen_len(v: Sized) -> Iterator[int32]:
    yield len(v)


async def coro_text(v: Stringable) -> str:
    await asyncio.sleep(0)
    return str(v)


async def coro_len(v: Sized) -> int32:
    await asyncio.sleep(0)
    return len(v)


def plain_len(v: Sized) -> int32:
    return len(v)


def show(tag: str, it: Iterator[str]) -> None:
    for s in it:
        print(tag, s)


def values() -> None:
    # int32: the frame holds its own copy.
    n = 5
    g1 = gen_hash(n)  # tpyc: ok
    n = 9
    for k in g1:
        print("int32", k)
    # BigInt
    b: int = 7
    g2 = gen_text(b)  # tpyc: ok
    b = 70
    show("bigint", g2)
    # literal
    show("literal", gen_text(3))  # tpyc: ok
    # char
    w = "xy"
    c: char = w[0]
    g3 = gen_text(c)  # tpyc: ok
    c = w[1]
    show("char", g3)
    # enum
    e = Color.RED
    g4 = gen_text(e)  # tpyc: ok
    e = Color.GREEN
    show("enum", g4)
    # ValueType record: a value type, copied into the frame as at its twin.
    p = P(1)
    g5 = gen_text(p)  # tpyc: ok
    p = P(5)
    show("valuetype", g5)
    # field read: the value read at the call.
    h = H(4)
    g6 = gen_text(h.n)  # tpyc: ok
    h.n = 40
    show("field", g6)
    # select over two locals: the frame copies the chosen value.
    a = 4
    a2 = 5
    g7 = gen_text(a if h.n > 0 else a2)  # tpyc: ok
    a = 8
    show("select", g7)


def refs() -> None:
    # reference-returning call (`max` returns an operand): the value is copied.
    a = 3
    b = 4
    g1 = gen_hash(max(a, b))  # tpyc: ok
    b = 10
    for k in g1:
        print("max", k)
    # range, a value-type iterable: the frame holds its own copy.
    r = range(3)
    g2 = gen_text(r)  # tpyc: ok
    r = range(7)
    show("range", g2)
    # record through a reference-returning call, a field read or a select:
    # the frame shares the record, so a later mutation through the source
    # reaches it.
    bx = Box(1)
    g3 = gen_text(get_r(bx))  # tpyc: ok
    bx.r.n = 11
    show("record call", g3)
    g4 = gen_text(bx.r)  # tpyc: ok
    bx.r.n = 12
    show("record field", g4)
    r1 = R(1)
    r2 = R(2)
    g5 = gen_text(r1 if bx.r.n > 0 else r2)  # tpyc: ok
    r1.n = 13
    show("record select", g5)


def texts() -> None:
    # str local: the frame owns its copy of the buffer.
    s = "abc"
    g1 = gen_len(s)  # tpyc: ok
    s = "wxyz!"
    for k in g1:
        print("str", k)
    # str local the body appends to (owned storage).
    o = "ab"
    o += "c"
    g2 = gen_len(o)  # tpyc: ok
    o += "defg"
    for k in g2:
        print("str owned", k)
    # str literal and call result: built straight into the frame.
    for k in gen_len("abcd"):  # tpyc: ok
        print("str literal", k)
    for k in gen_len(make_str()):  # tpyc: ok
        print("str call", k)
    # bytes
    bs = b"xy"
    g3 = gen_len(bs)  # tpyc: ok
    bs = b"wxyz"
    for k in g3:
        print("bytes", k)


def tuples() -> None:
    # value tuple: copied.
    t = (1, "a")
    g1 = gen_text(t)  # tpyc: ok
    t = (2, "b")
    show("tuple", g1)
    # tuple with a record element: the record stays shared.
    tr = (1, R(2))
    g2 = gen_text(tr)  # tpyc: ok
    tr[1].n = 7
    tr = (5, R(5))
    show("tuple record", g2)
    # tuple literal with a fresh record: the record is seated for the frame.
    show("tuple literal", gen_text((1, R(3))))  # tpyc: ok
    # value tuple at a user subprotocol: the same twin as at Stringable.
    ts = (1, "a")
    g3 = gen_sub(ts)  # tpyc: ok
    ts = (2, "b")
    show("subprotocol tuple", g3)


def pointers() -> None:
    # Optional value narrowed by its assignment: the payload is copied.
    ov: int32 | None = 4
    g1 = gen_text(ov)  # tpyc: ok
    ov = None
    show("optional value", g1)
    # Optional record: the record stays shared.
    orr = maybe_r(1)
    g2 = gen_text(orr)  # tpyc: ok
    if orr is not None:
        orr.n = 9
    orr = maybe_r(5)
    show("optional record", g2)
    # union holding a record: the record stays shared.
    u = pick(2)
    g3 = gen_text(u)  # tpyc: ok
    if isinstance(u, R):
        u.n = 8
    u = pick(-3)
    show("union", g3)


def optional_view(s: str | None) -> None:
    # un-narrowed `str | None` view parameter: the frame owns its copy.
    g = gen_text(s)  # tpyc: ok
    s = "zz"
    show("optional view", g)


def aliases() -> None:
    # record: shared.
    r = R(1)
    g1 = gen_text(r)  # tpyc: ok
    r.n = 9
    show("record", g1)
    # container: shared.
    ys = [1]
    g2 = gen_len(ys)
    # The frame holds the list itself, so this warning is a false one
    # (BUGS.md#frame-held-container-mutation-warns).
    ys.append(3)  # tpyc: warning(/Mutation of 'ys' while borrowed/)
    for k in g2:
        print("container", k)


def plain_views() -> None:
    # Span local at a plain call: the callee reads the view within the call.
    xs = [1, 2, 3]
    sp: Span[int32] = xs
    print("plain span", plain_len(sp))  # tpyc: ok


def plain_pack(*args: int32) -> None:
    # *args pack at a plain call.
    print("plain args", plain_len(args))  # tpyc: ok


def relay() -> Iterator[str]:
    # A generator body hands its own locals to a frame, then rebinds them
    # before that frame reads them.
    n = 5
    for s in gen_twice(n):  # tpyc: ok
        n = 6
        yield s
    s2 = "abc"
    for k in gen_len(s2):  # tpyc: ok
        s2 = "x"
        yield str(k)


async def coros() -> None:
    n = 5
    c1 = coro_text(n)  # tpyc: ok
    n = 9
    print("async int32", await c1)
    s = "abc"
    c2 = coro_len(s)  # tpyc: ok
    s = "wxyz!"
    print("async str", await c2)
    print("async str literal", await coro_len("ab"))  # tpyc: ok
    print("async str call", await coro_len(make_str()))  # tpyc: ok
    h = H(4)
    c3 = coro_text(h.n)  # tpyc: ok
    h.n = 40
    print("async field", await c3)
    t = (1, "a")
    c4 = coro_text(t)  # tpyc: ok
    t = (2, "b")
    print("async tuple", await c4)
    tr = (1, R(2))
    c5 = coro_text(tr)  # tpyc: ok
    tr[1].n = 7
    print("async tuple record", await c5)
    orr = maybe_r(1)
    c6 = coro_text(orr)  # tpyc: ok
    if orr is not None:
        orr.n = 9
    print("async optional record", await c6)
    u = pick(2)
    c7 = coro_text(u)  # tpyc: ok
    if isinstance(u, R):
        u.n = 8
    print("async union", await c7)
    r = R(1)
    c8 = coro_text(r)  # tpyc: ok
    r.n = 9
    print("async record", await c8)
    # select: the chosen value is copied.
    a = 4
    a2 = 5
    c9 = coro_text(a if n > 0 else a2)  # tpyc: ok
    a = 8
    print("async select", await c9)
    # int32 still read after the rebind (no last-use move): copied.
    k = 5
    c10 = coro_text(k)  # tpyc: ok
    k += 4
    print("async int32 kept", await c10, k)
    # bytes: the frame owns its copy.
    bs = b"xy"
    c11 = coro_text(bs)  # tpyc: ok
    bs += b"z"
    print("async bytes", await c11, len(bs))
    # reference-returning call: the value is copied.
    m1 = 3
    m2 = 4
    c12 = coro_text(max(m1, m2))  # tpyc: ok
    m2 = 10
    print("async max", await c12)
    # record through a reference-returning call: shared.
    bx = Box(1)
    c14 = coro_text(get_r(bx))  # tpyc: ok
    bx.r.n = 11
    print("async record call", await c14)
    # range: copied into the frame (an async body holds no range local yet,
    # BUGS.md#frame-range-local-rejects, so the argument is a fresh range).
    print("async range", await coro_text(range(3)))  # tpyc: ok


def main() -> None:
    values()
    refs()
    texts()
    tuples()
    pointers()
    optional_view("abc")
    aliases()
    plain_views()
    plain_pack(1, 2, 3)
    show("relay", relay())
    asyncio.run(coros())


main()
