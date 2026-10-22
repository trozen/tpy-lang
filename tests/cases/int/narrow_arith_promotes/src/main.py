# 8/16-bit `+ - * // % ** << ~` and unary `-` compute in the default int
# (CPython's value); `& | ^ >>` keep the narrow type; any integer stores into
# any fixed-width slot through one checked narrowing, at every position.
from __future__ import annotations
from typing import Final, Iterator
from typing import Literal
from tpy import (int8, int16, int32, int64, uint8, uint16, uint32, uint64,
                 error_return, ReturnException, Fn, dispatch, BytesView)
from tpy.atomic import Atomic
import asyncio


class Failed(Exception, ReturnException):
    pass


class Ctx:
    def __enter__(self) -> Ctx:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Counter:
    f8: int8

    # constructor: a field initialized from a promoted value narrows into it
    def __init__(self, v: int8) -> None:
        self.f8 = v + 1  # tpyc: ok

    # method: a field aug-assign computes in the default int, then narrows
    def bump(self, by: int8) -> int8:
        self.f8 += by  # tpyc: ok
        return self.f8


# free function: the operators, the narrowing slots, the literal warning
def free_fn(a8: int8, b8: int8, d: bytes, u16: uint16, i32: int32, y: int) -> None:
    s = a8 + b8  # tpyc: type(int32)
    m = d[0] + 100  # tpyc: type(int32)
    inv = ~d[0]  # tpyc: type(int32)
    neg = -uint8(200)  # tpyc: type(int32)
    low = d[0] & 0x0F  # tpyc: type(uint8)
    wide = d[0] & 0x1FF  # tpyc: type(int32)
    big = a8 * 3000000000  # tpyc: type(int) warning(/outside default int32 range; the operation computes in int/)
    print("free", s, m, inv, neg, low, wide, big)
    x8: int8 = u16  # tpyc: ok
    x: uint32 = i32  # tpyc: ok
    z8: int8 = -100
    z8 += y  # tpyc: ok
    print("free slots", x8, x, z8)
    # an inferred local takes the join of its bindings
    c = d[1]  # tpyc: type(int32)
    c -= 48
    # a literal-seeded shift count leaves the 8/16-bit operator its own result
    count = 1
    sh = a8 << count  # tpyc: type(int32)
    print("free join", c, sh, pow(a8, b8 // b8 + 1), divmod(int8(-7), int8(2)))
    # pow() / divmod() compute in the default int, as `**`, `//`, `%` do
    p8 = pow(int8(5), int8(3))  # tpyc: type(int32)
    p16 = pow(int16(300), int16(2))  # tpyc: type(int32)
    e8 = a8 ** 2  # tpyc: type(int32)
    m8 = a8 % b8  # tpyc: type(int32)
    q, r = divmod(int8(-7), int8(2))
    r8: int8 = pow(int8(2), int8(6))  # tpyc: ok
    print("free pow", p8, p16, e8, m8, q, r, r8)
    # `>> | ^` and unary `+` keep the narrow type
    s8 = a8 >> 1  # tpyc: type(int8)
    o8 = a8 | b8  # tpyc: type(int8)
    x8b = a8 ^ b8  # tpyc: type(int8)
    pos8 = +a8  # tpyc: type(int8)
    print("free bitwise", s8, o8, x8b, pos8)
    # an explicit dunder call renders as the operator does
    print("free dunder", int8(100).__add__(b8), int8(100).__neg__())
    # a value-range-proven conversion renders as a plain cast, implicit or explicit
    if i32 >= 0:
        nn: uint32 = i32  # tpyc: cast_safe(uint32)
        print("free cast", nn, uint32(i32))  # tpyc: cast_safe(uint32)


# generator: a promoted value narrows at the yield
def gen(a8: int8) -> Iterator[int8]:
    yield a8 + 1  # tpyc: ok
    yield a8 - 1  # tpyc: ok


# async: a promoted value narrows at the return after a suspension
async def async_fn(a8: int8, b8: int8) -> int8:
    await asyncio.sleep(0)
    return a8 - b8  # tpyc: ok


# comprehension: the element computes in the default int; a uint8 list narrows it
def comp(d: bytes) -> None:
    ys = [x + 1 for x in d]  # tpyc: type(list[int32])
    zs: list[uint8] = [x + 1 for x in d]  # tpyc: ok
    print("comp", ys, zs)


# closure: the nested def's promoted result narrows at its return
def closure(a8: int8) -> int8:
    def inner(v: int8) -> int8:
        return v + a8  # tpyc: ok
    return inner(3)


# context-manager body: a promoted product
def cm_body(u16: uint16, v16: uint16) -> int32:
    with Ctx():
        r = u16 * v16  # tpyc: type(int32)
    return r


# try/finally: a 16-bit sum past int16 is exact
def try_finally(a16: int16) -> int32:
    try:
        r = a16 + a16  # tpyc: type(int32)
    finally:
        print("try_finally done")
    return r


# @error_return body: the promoted value narrows at the return
@error_return(Failed)
def er_body(a8: int8) -> int8:
    if a8 > 100:
        raise Failed()
    return a8 * 2  # tpyc: ok


# match arm: the guard computes in the default int
def match_arm(a8: int8) -> str:
    match a8:
        case n if n + 100 > 200:  # tpyc: ok
            return "big"
        case _:
            return "small"


# container element and Optional payload slots narrow; a tuple literal per element
def slots(a8: int8, b8: int8) -> None:
    xs8: list[int8] = [a8 + 1, b8]  # tpyc: ok
    xs8.append(a8 + b8)  # tpyc: ok
    d8: dict[int8, int8] = {}
    d8[a8] = a8 - b8  # tpyc: ok
    t: tuple[int8, int8] = (a8 + 1, b8)  # tpyc: ok
    o: int8 | None = a8 + 2  # tpyc: ok
    print("slots", xs8, d8[a8], t, o is None)


# lookups: an int32 key narrows into an int8 container (warned); `in` compares
# by value, and integers of different types compare by value
def lookups(i: int32, out: int32, j: int32) -> None:
    xs8: list[int8] = [44, 1]
    d8: dict[int8, int32] = {44: 7}
    s8: set[int8] = {44}
    print("lookups in", out in xs8, out in d8, out in s8, i in xs8)
    print("lookups", xs8.index(i), xs8.count(i), d8[i])  # tpyc: warning(/lookup key narrows from int32 to int8/) warning(/lookup key narrows/) warning(/lookup key narrows/)
    xs32: list[uint32] = [4294967295]
    ys32: list[uint32] = [1]
    print("lookups by value", j in xs32, j in ys32)


# bytearray: an int32 outside range(0, 256) raises CPython's ValueError
def bytes_store(i32: int32, u8: uint8) -> None:
    ba = bytearray(b"a")
    ba.append(u8)
    ba.append(i32)  # tpyc: ok
    try:
        ba.append(i32 + 300)
    except ValueError:
        print("bytes ValueError")
    print("bytes", ba)


class Acc:
    n32: int32

    def __init__(self) -> None:
        self.n32 = 0

    def add(self, a64: int64) -> None:
        self.n32 += a64  # tpyc: ok


class Box[T]:
    val: T

    def __init__(self) -> None:
        pass

    def set(self, val: T) -> None:
        self.val = val


class S:
    def __init__(self) -> None:
        pass

    def __mul__(self, x: int8) -> int32:
        return x


def take64(x: uint64) -> uint64:
    return x


def w32(v: int32) -> int32:
    return v


@dispatch
def apply_one[T, U](f: Fn[[int32], U], x: T) -> int32:
    return 1


@dispatch
def apply_one[T, U](f: Fn[[str], U], x: T) -> int32:
    return 1


def pick(f: bool) -> Literal[300, 5]:
    if f:
        return 300
    return 5


CONST = 5


# stores that used to be refused: a wider or sign-changing integer converts
# into a declared slot (checked), and an inferred local joins its bindings
def converts(a32: int32, a64: int64, wide: int64) -> None:
    x8: int8 = 0
    x8 = int16(3)  # tpyc: ok
    i16: int16 = 2
    i16 += int64(3)  # tpyc: ok
    acc = Acc()
    acc.add(a64)
    print("converts declared", x8, i16, acc.n32)
    j = a32  # tpyc: type(int64)
    j += a64
    k = a32  # tpyc: type(int64)
    k = a64
    m = int8(3)  # tpyc: type(int64)
    m += wide
    q = int8(3)  # tpyc: type(int32)
    q = 200
    print("converts joined", j, k, m, q)
    a = 0
    b = 0
    b = w32(7)
    print("converts unsigned", take64(a), take64(b), take64(CONST))  # tpyc: ok
    c = Box()
    c.set(int32(1))
    p = 1
    t: int8 = 7
    t = p  # tpyc: ok
    print("converts trial", apply_one(lambda v: v + p, 0), t, c.val)
    p = w32(2)
    ys = [1, 300]
    n: int8 = ys[0]  # tpyc: ok
    s = S()
    print("converts element", n, s * pick(False))  # tpyc: ok
    data = b"hello"
    try:
        print(256 in data)
    except ValueError:
        print("converts bytes ValueError")


# an unannotated loop, unpack or match target joins its stores, as a local does
def targets(d: bytes, p: tuple[uint8, uint8], v: int8) -> None:
    for c in d:  # tpyc: type(int32)
        c += 1  # tpyc: ok
        print("targets loop", c)
    for c in d:
        c = c + 1  # tpyc: ok
        print("targets loop assign", c)
    a, b = p  # tpyc: type(int32)
    a += 1  # tpyc: ok
    print("targets unpack", a, b)
    match v:
        case m:
            m += 1  # tpyc: ok
            print("targets match", m)


# generator: the frame slot of a loop target is declared at its join
def targets_gen(d: bytes) -> Iterator[int32]:
    for c in d:  # tpyc: type(int32)
        c += 1  # tpyc: ok
        yield c


# async: the frame slot of a loop target is declared at its join
async def targets_async(d: bytes) -> int32:
    t = 0
    for c in d:  # tpyc: type(int32)
        c += 1  # tpyc: ok
        await asyncio.sleep(0)
        t = c
    return t


# a repeated unpack, a loop over a name bound before it, and a walrus join
# every binding of the local
def targets_rebound(p: tuple[int8, int8], q: tuple[int64, int64],
                    d: bytes, v32: int32, end: int) -> None:
    a, b = p  # tpyc: type(int64)
    a, b = q  # tpyc: ok
    print("targets reunpack", a, b)
    c = d[0]  # tpyc: type(int32)
    for c in d:
        c += 1  # tpyc: ok
    print("targets prebound", c)
    n = d[0]  # tpyc: type(int32)
    if (n := n + 1) > 0:  # tpyc: ok
        print("targets walrus", n)
    # a name two sibling loop bodies bind as int32, which a later loop head
    # rebinds to `int` elements: one `int` local
    total = 0
    for i in range(1, 3):
        k = v32 * 10 + i  # tpyc: type(int)
        total += k
    for i in range(1, 3):
        k = v32 * 10 + i
        total += k
    for k in range(2, end):  # tpyc: ok
        total += k
    print("targets loop int", total + k)


# a walrus as a local's first binding declares it at the join of its bindings,
# two walruses in one statement are two bindings, and a local bound to one
# whose own bindings join joins its bindings too
def targets_walrus(v: uint8) -> None:
    if (w := v) > 0:
        w += 1
    print("walrus first", w)
    if (x := uint8(255)) > 0 and (x := x + 1) > 0:  # tpyc: ok
        print("walrus twice", x)
    y = uint8(255)
    y = uint8(254)
    e, f = (y, y)  # tpyc: type(int32)
    e += 1  # tpyc: ok
    g = y  # tpyc: type(int32)
    g += 2  # tpyc: ok
    zs = [1, 2]
    for z in zs:  # tpyc: type(int64)
        z = int64(5000000000)  # tpyc: ok
    print("walrus pending source", e, f, g, x, z, zs)


# generator: a walrus's frame slot is declared at the join of its bindings
def targets_walrus_gen(v: uint8) -> Iterator[int32]:
    if (w := v) > 0:
        w += 1
    yield w


# a binding whose value binds the same name first is one more binding of that
# local: the local is the join of both
def nested_bindings() -> None:
    x = (x := uint8(255)) + 1  # tpyc: type(int32)
    print("nested assign", x + 0)
    if (y := (y := uint8(255)) + 1) > 0:  # tpyc: ok
        print("nested walrus", y)
    a, z = 1, (z := uint8(255)) + 1  # tpyc: ok
    print("nested unpack", a, z + 0)


# generator: the frame slot of a nested binding is declared at the join
def nested_bindings_gen() -> Iterator[int32]:
    x = (x := uint8(255)) + 1
    yield x + 0
    if (y := (y := uint8(255)) + 1) > 0:
        yield y


class Prop:
    _v: int8

    def __init__(self) -> None:
        self._v = 0

    @property
    def v(self) -> int8:
        return self._v

    @v.setter
    def v(self, x: int8) -> None:
        self._v = x


# a property setter receives the narrowed value; an Atomic argument narrows too
def setters(big: int64, at: Atomic[int8], wide: int32) -> None:
    p = Prop()
    p.v = big  # tpyc: ok
    print("setters", p.v)
    try:
        at.store(wide)
    except OverflowError:
        print("setters atomic store OverflowError")
    try:
        at.fetch_add(wide)
    except OverflowError:
        print("setters atomic fetch_add OverflowError")
    print("setters atomic", at.load())
    try:
        at.compare_exchange(wide, 1)
    except OverflowError:
        print("setters atomic compare_exchange OverflowError")
    try:
        at.compare_exchange_weak(wide, 1)
    except OverflowError:
        print("setters atomic compare_exchange_weak OverflowError")


# a literal operation retyped to its int8 slot divides at int8 and raises
# ZeroDivisionError as CPython does
def literal_retype() -> None:
    try:
        x8: int8 = 5 // 0  # tpyc: ok
        print("retype", x8)
    except ZeroDivisionError:
        print("retype ZeroDivisionError")


# a lookup key whose type a later store decides warns too; an explicit conversion silences it
def lookup_keys(i: int32) -> None:
    xs8: list[int8] = [44, 1]
    s8: set[int8] = {44}
    n = int8(44)
    print("keys", xs8.count(n))  # tpyc: warning(/lookup key narrows from int64 to int8/)
    n = int64(1)
    s8.discard(i)  # tpyc: warning(/lookup key narrows from int32 to int8/)
    print("keys", xs8.count(int8(i)), 44 in s8)  # tpyc: ok


# bytearray / bytes: an integer of any width outside range(0, 256) raises ValueError
def bytes_widths(u32: uint32, i64: int64, u64: uint64, big: int, i32: int32) -> None:
    ba = bytearray(b"ab")
    bv: BytesView = b"ab"
    for k in range(4):
        try:
            if k == 0:
                ba.append(u32)
            elif k == 1:
                ba.append(i64)
            elif k == 2:
                ba.append(u64)
            else:
                ba.append(big)
        except ValueError:
            print("bytes append", k, "ValueError")
    try:
        ba.insert(0, i32)
    except ValueError:
        print("bytes insert ValueError")
    try:
        ba.remove(i64)
    except ValueError:
        print("bytes remove ValueError")
    try:
        print(i64 in bv)
    except ValueError:
        print("bytes view contains ValueError")
    print("bytes", ba)


# module-level statement: a global aug-assign narrows into the global; a Final
# computed from another stays a compile-time constant, transitively
G8: Final[int8] = 100
G16: Final[int16] = G8 + 1  # tpyc: ok
G32: Final[int32] = G16  # tpyc: ok
g8: int8 = 5
g8 += 1  # tpyc: ok
# module level: a global annotated at its first binding takes the wider values
# stored into it later (an unannotated one keeps its first type and refuses them)
w8: int32 = uint8(255)
for i in range(2):
    wt = (w8, 1)
    w8 = w8 + 1  # tpyc: ok
    print("module global", wt, (w8 := w8 + 1))  # tpyc: ok
print("module global after", w8)
# module level: a global first bound to a literal joins its bindings, a walrus
# store's as an assignment's, so the wider value is not truncated
lw = 0
print("module literal walrus", (lw := int64(5000000000)))  # tpyc: ok
for i in range(1):
    print("module literal walrus loop", (lw := int64(6000000000)))  # tpyc: ok
print("module literal walrus after", lw)
# module level: a walrus inside a comprehension widens the global it binds,
# not the comprehension's own scope
cw = 0
cws = [(cw := int64(5000000000)) for i in range(1)]  # tpyc: ok
print("module comprehension walrus", cws, cw)


class Consts:
    X: Final[int8] = G8 + 1  # tpyc: ok
    Y: Final[int16] = X


def main() -> None:
    free_fn(100, 100, b"\xff\x28", 100, 7, 200)
    print("gen", list(gen(10)))
    print("async", asyncio.run(async_fn(-100, 28)))
    comp(b"\x01\xfe")
    print("closure", closure(4))
    print("cm", cm_body(60000, 2))
    tf = try_finally(30000)
    print("try", tf)
    try:
        print("er", er_body(50))
    except Failed:
        print("er Failed")
    print("match", match_arm(127), match_arm(1))
    c = Counter(10)
    print("method", c.bump(5))
    slots(10, 20)
    lookups(44, 300, -1)
    bytes_store(66, 67)
    v = 300
    try:
        print(int8(v))
    except OverflowError:
        print("ctor OverflowError")
    print("module", g8, G16, G32, Consts.X, Consts.Y)
    converts(1, 2, 5)
    targets(b"\xff", (255, 1), 127)
    print("targets gen", list(targets_gen(b"\xff")))
    print("targets async", asyncio.run(targets_async(b"\xff")))
    targets_rebound((1, 2), (5000000000, -5000000000), b"\xff", 3, 5)
    targets_walrus(255)
    print("walrus gen", list(targets_walrus_gen(255)))
    nested_bindings()
    print("nested gen", list(nested_bindings_gen()))
    setters(100, Atomic(int8(1)), 300)
    lookup_keys(44)
    literal_retype()
    bytes_widths(4000000000, -1, 300, 2 ** 70, 256)


main()
