# bytearray `+=` / `*=` update the object in place (CPython's __iadd__ /
# __imul__), so an alias, a caller and a field holder all see the change;
# in-place ops on locals that are also rebound (bytearray, list, set `&=`,
# a record `__iadd__`), direct dunder calls and a module-level target.
import asyncio
from typing import Iterator
from tpy import BytesView, readonly


def mk() -> bytes:
    return b"mk"


# free function: `+=` with bytes, bytearray and BytesView operands
def free_ops() -> None:
    ba = bytearray(b"a")
    ba += b"b"  # tpyc: ok
    other = bytearray(b"c")
    ba += other  # tpyc: ok
    src = b"d"
    v: BytesView = src
    ba += v  # tpyc: ok
    ba += mk()  # tpyc: ok
    print("free", ba)


# alias: the update is visible through a second name
def alias_ops() -> None:
    ba = bytearray(b"x")
    al = ba
    ba += b"y"  # tpyc: ok
    print("alias", al, ba, al is ba)


# `*=`: n > 1 repeats, 1 keeps, n <= 0 empties -- all in place
def imul_ops() -> None:
    for n in [2, 1, 0, -1]:
        ba = bytearray(b"ab")
        al = ba
        ba *= n  # tpyc: ok
        print("imul", n, al)


# parameter: the caller's object grows
def grow(p: bytearray) -> None:
    p += b"P"  # tpyc: ok


def param_ops() -> None:
    q = bytearray(b"q")
    grow(q)
    print("param", q)


# method on a field target
class Buf:
    buf: bytearray

    def __init__(self) -> None:
        self.buf = bytearray(b"f")

    def add(self) -> None:
        self.buf += b"!"  # tpyc: ok
        self.buf *= 2  # tpyc: ok
        print("method", bytes(self.buf))


# generator body
def gen_ops(ba: bytearray) -> Iterator[int]:
    ba += b"g"  # tpyc: ok
    yield len(ba)
    ba *= 2  # tpyc: ok
    yield len(ba)


# async body
async def async_ops(ba: bytearray) -> int:
    ba += b"s"  # tpyc: ok
    await asyncio.sleep(0)
    ba *= 3  # tpyc: ok
    return len(ba)


# try/finally
def try_ops() -> None:
    ba = bytearray(b"t")
    try:
        ba += b"r"  # tpyc: ok
    finally:
        ba += b"y"  # tpyc: ok
    print("try", ba)


# loop
def loop_ops() -> None:
    ba = bytearray()
    for i in range(3):
        ba += b"L"  # tpyc: ok
    print("loop", ba)


# pending-bytes local operand (the local's own type is decided by its uses)
def pending_ops() -> None:
    ba = bytearray(b"p")
    o = b"end"
    ba += o  # tpyc: ok
    print("pending", ba, o)


# a bytes concat expression operand
def concat_ops() -> None:
    body = bytearray()
    bnd = b"B"
    body += b"--" + bnd + b"\r\n"  # tpyc: ok
    print("concat", body)


# a local also plainly rebound is written through (bytearray and list alike)
def rebound_ops(again: bool) -> None:
    ba = bytearray(b"r")
    al = ba
    ba += b"b"  # tpyc: ok
    xs = [1]
    xl = xs
    xs += [2]  # tpyc: ok
    if again:
        ba = bytearray(b"new")
        xs = [9]
    print("rebound", al, ba, xl, xs)


# rebound locals: a set `&=` with a literal operand, a user record `__iadd__`
def rebound_more(again: bool) -> None:
    s = {1, 2}
    sl = s
    s &= {2}  # tpyc: ok
    if again:
        s = {7}
    r = Counter(1)
    rl = r
    r += 5  # tpyc: ok
    if again:
        r = Counter(9)
    r += 1  # tpyc: ok
    print("rebound_more", sl, s, rl.n, r.n)


class Counter:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def __iadd__(self, k: int) -> "Counter":
        self.n += k
        return self


# operand viewing the target's own buffer: TPy's slice is a view into `ba`,
# so the append reads from the buffer it grows (forcing a reallocation)
def overlap_ops() -> None:
    ba = bytearray(b"abcd")
    ba += ba[1:3]  # tpyc: ok
    ba += ba[0:6]  # tpyc: ok
    print("overlap", ba)


# generator body with a local that is both rebound and `+=`'d
def gen_rebound(again: bool) -> Iterator[bytearray]:
    buf = bytearray(b"g")
    held = buf
    buf += b"1"  # tpyc: ok
    yield held
    if again:
        buf = bytearray(b"n")
    buf += b"2"  # tpyc: ok
    yield buf


# generator body with a record local that is rebound and then `+=`'d
def gen_record_rebound(again: bool) -> Iterator[int]:
    r = Counter(1)
    rl = r
    if again:
        r = Counter(10)
    r += 2  # tpyc: ok
    yield rl.n
    yield r.n


# `*=` on a bytearray local that is also rebound
def imul_rebound(again: bool) -> None:
    ba = bytearray(b"ab")
    al = ba
    if again:
        ba = bytearray(b"z")
    ba *= 3  # tpyc: ok
    print("imul_rebound", al, ba)


# the dunders called directly hand back the receiver itself
def direct_ops() -> None:
    ba = bytearray(b"d")
    c = ba.__iadd__(b"y")  # tpyc: ok
    print("direct iadd", c, c is ba)
    bb = bytearray(b"m")
    m = bb.__imul__(2)  # tpyc: ok
    print("direct imul", m, m is bb)
    xs = [1]
    lc = xs.__iadd__([2])  # tpyc: ok
    lc.append(3)
    print("direct list", xs, lc is xs)


# a readonly bytearray operand
def readonly_ops(src: readonly[bytearray]) -> None:
    ba = bytearray(b"o")
    ba += src  # tpyc: ok
    print("readonly", ba)


# module-level target (a global)
g_buf = bytearray(b"G")
g_buf += b"!"  # tpyc: ok
g_buf *= 2  # tpyc: ok


def main() -> None:
    free_ops()
    alias_ops()
    imul_ops()
    param_ops()
    Buf().add()
    g = bytearray(b"")
    for n in gen_ops(g):
        print("gen", n, g)
    a = bytearray(b"a")
    print("async", asyncio.run(async_ops(a)), a)
    try_ops()
    loop_ops()
    pending_ops()
    concat_ops()
    rebound_ops(False)
    rebound_ops(True)
    rebound_more(False)
    rebound_more(True)
    overlap_ops()
    for b in gen_rebound(False):
        print("gen_rebound", b)
    for b in gen_rebound(True):
        print("gen_rebound", b)
    for n in gen_record_rebound(False):
        print("gen_record_rebound", n)
    for n in gen_record_rebound(True):
        print("gen_record_rebound", n)
    imul_rebound(False)
    imul_rebound(True)
    direct_ops()
    r_src = bytearray(b"R")
    readonly_ops(r_src)
    print("global", g_buf)


main()
