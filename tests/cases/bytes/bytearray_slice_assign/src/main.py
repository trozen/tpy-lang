# bytearray slice assignment (basic, stepped, overlapping self-slices) on
# every tracked receiver shape, and list slice sources that view their target.
# Slice reads print through bytes(...): a bytearray slice is not a bytearray
# (BUGS.md#bytearray-slice-not-bytearray).
import asyncio
from typing import Iterator

from tpy import Array, int32, int64, uint8

import slice_globals

# module: a global bytearray as slice target and receiver
g = bytearray(b"global")
g[0:2] = b"GL"  # tpyc: ok
print("module:", g, bytes(g[1:3]))


class Mem:
    mem: bytearray

    def __init__(self) -> None:
        self.mem = bytearray(b"abcdefgh")

    def move(self, d: int32, s: int32, n: int32) -> None:
        # Field target and field slice read in one statement.
        self.mem[d:d + n] = self.mem[s:s + n]  # tpyc: ok


def fill(b: bytearray, n: int32) -> None:
    # Parameter target: the caller sees the write.
    b[0:n] = bytes([42]) * n  # tpyc: ok


def head(b: bytearray) -> bytes:
    # Parameter receiver of a slice read.
    return bytes(b[0:2])  # tpyc: ok


def letters() -> Iterator[uint8]:
    yield 65
    yield 66
    yield 67


def noisy() -> Iterator[uint8]:
    print("zero_step: generator ran")
    yield 65


async def patch_async() -> None:
    # Async frame slot as target and receiver.
    buf = bytearray(b"async")
    buf[0:2] = bytes(buf[3:5])  # tpyc: ok
    print("async:", buf)


def frame(n: int32) -> Iterator[int32]:
    f = bytearray(b"gen")
    yield len(f)
    # Generator frame slot as target and receiver.
    f[0:1] = bytes(f[1:3]) * n  # tpyc: ok
    yield len(f)
    print("frame:", f)


def main() -> None:
    # local: same-length overwrite (the literal is copied into a heap buffer
    # first, BUGS.md#slice-assign-bytes-literal-owned)
    a = bytearray(b"abcdef")
    a[1:3] = b"XY"  # tpyc: ok
    print("local:", a)

    # resize: grow, then shrink
    a[0:1] = b"123"  # tpyc: ok
    print("grow:", a)
    a[0:5] = b"z"  # tpyc: ok
    print("shrink:", a)

    # whole: replace everything
    a[:] = b"whole"  # tpyc: ok
    print("whole:", a)

    # clamped: out-of-range bounds clamp onto a same-length slice
    a[3:100] = b"xy"  # tpyc: ok
    a[-100:2] = b"pq"  # tpyc: ok
    print("clamped:", a)

    # reversed: an empty reversed range with an empty RHS changes nothing
    a[4:1] = b""  # tpyc: ok
    print("reversed:", a)

    # insert_delete: an empty slice inserts, an empty RHS deletes
    a[2:2] = b"xy"  # tpyc: ok
    print("insert:", a)
    a[1:3] = b""  # tpyc: ok
    print("delete:", a)

    # stepped: same length as the extended slice, forward and backward
    s = bytearray(b"whole")
    s[::2] = b"123"  # tpyc: ok
    print("stepped:", s)
    s[::-1] = b"vwxyz"  # tpyc: ok
    print("stepped_neg:", s)
    try:
        s[0:4:2] = b"xyz"  # tpyc: ok
        print("stepped_mismatch: no error")
    except ValueError as e:
        print("stepped_mismatch:", e)

    # stepped_delete: an empty RHS deletes the selected bytes, both signs
    d = bytearray(b"abcdef")
    d[::2] = b""  # tpyc: ok
    print("stepped_delete:", d)
    d = bytearray(b"abcdef")
    d[::-2] = b""  # tpyc: ok
    print("stepped_delete_neg:", d)
    d = bytearray(b"abcdefgh")
    d[6:0:-3] = b""  # tpyc: ok
    print("stepped_delete_bounded:", d)

    # rhs: bytes value, bytearray, bytes literal, repeated byte, int list,
    # and a generator into a stepped slice
    r = bytearray(b"--------")
    bs = bytes(b"BY")
    r[0:2] = bs  # tpyc: ok
    r[2:4] = bytearray(b"ba")  # tpyc: ok
    r[4:5] = b"L"  # tpyc: ok
    c = 33
    r[5:6] = bytes([c]) * 2  # tpyc: ok
    r[7:8] = [65, 66]  # tpyc: ok
    print("rhs:", r)
    r[0:6:2] = letters()  # tpyc: ok
    print("rhs_gen:", r)

    # overlap: a slice of the target as the source, growing and shifting
    o = bytearray(b"abcdefgh")
    o[0:2] = o[0:5]  # tpyc: ok
    print("overlap_grow:", o)
    p = bytearray(b"abcdefgh")
    p[2:6] = p[0:4]  # tpyc: ok
    print("overlap_right:", p)
    p[0:4] = p[2:6]  # tpyc: ok
    print("overlap_left:", p)
    p[2:4] = p[2:4]  # tpyc: ok
    p[:] = p  # tpyc: ok
    print("overlap_self:", p)
    p[1:3] = p  # tpyc: ok
    print("overlap_whole:", p)

    # field: a method moves bytes inside its own buffer
    m = Mem()
    m.move(0, 4, 3)
    print("field:", bytes(m.mem))

    # param: the caller sees the write, and a read off the parameter
    q = bytearray(b"param")
    fill(q, 2)
    print("param:", q, head(q))

    # reassigned: a rebound local as read and assign receiver
    t = bytearray(b"first")
    print("reassigned_first:", len(t))
    t = bytearray(b"second")
    t[0:2] = t[4:6]  # tpyc: ok
    print("reassigned:", t, bytes(t[1:3]))  # tpyc: ok

    # generator: a frame-slot receiver
    for k in frame(2):
        print("frame_len:", k)

    # cross_module: module-attribute receivers of a read and an assignment
    print("cross_module_str:", slice_globals.NAME[1:3])  # tpyc: ok
    print("cross_module_bytes:", slice_globals.DATA[1:5:2])  # tpyc: ok
    slice_globals.NUMS[1:3] = [80, 90, 100]  # tpyc: ok
    print("cross_module_list:", slice_globals.NUMS)

    # list: a slice of the target as the source
    xs = [0, 1, 2, 3, 4, 5, 6, 7]
    # Keeps `xs` a list rather than a std::array (BUGS.md#slice-assign-array-demoted-local).
    xs.append(8)
    xs[0:2] = xs[0:5]  # tpyc: ok
    print("list_grow:", xs)
    xs[1:4] = xs[0:3]  # tpyc: ok
    print("list_right:", xs)
    xs[0:3] = xs[1:4]  # tpyc: ok
    print("list_left:", xs)

    # list_bool: self-assignment through the whole-list alias guard
    flags = [True, False, True, False]
    flags.append(True)
    flags[1:3] = flags  # tpyc: ok
    print("list_bool:", flags)

    # list_stepped_empty: an empty value into a list's extended slice raises
    # (a bytearray deletes instead)
    ys = [1, 2, 3, 4, 5]
    ys.append(6)
    try:
        ys[::2] = []  # tpyc: ok
        print("list_stepped_empty: no error")
    except ValueError as e:
        print("list_stepped_empty:", e, ys)

    # list_mixed: the source's element type converts to the target's
    wide: list[int64] = [1, 2, 3]
    wide.append(4)
    narrow: list[int32] = [8, 9]
    narrow.append(10)
    wide[0:3] = narrow  # tpyc: ok
    bits = [True, False, True]
    bits.append(False)
    pair: Array[bool, 2] = [False, True]
    bits[1:3] = pair  # tpyc: ok
    print("list_mixed:", wide, bits)

    # stepped_one: a step of 1 resizes like a basic slice
    u = bytearray(b"abcdef")
    u[0:2:1] = b"xyz"  # tpyc: ok
    print("stepped_one:", u)

    # stepped_self: a view of the target into an extended slice
    v = bytearray(b"abcdef")
    v[::2] = v[1:4]  # tpyc: ok
    print("stepped_self:", v)

    # zero_step: the step is rejected before the generator runs
    z = bytearray(b"abcd")
    step = 0
    try:
        z[::step] = noisy()  # tpyc: ok
    except ValueError as e:
        print("zero_step:", e, z)

    # closure: a nested def assigns into a captured bytearray
    cap = bytearray(b"outer")

    def patch() -> None:
        cap[0:2] = b"OU"  # tpyc: ok

    patch()
    print("closure:", cap)

    # async: a slice assignment inside a coroutine
    asyncio.run(patch_async())


main()
