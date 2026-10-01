# A local's type is declared by its first binding: a bare literal there seeds
# its family's default, widened by the typed values stored later and decided
# once its function is analyzed -- every use, the ones before the typed store
# included, computes at that type -- while a value there gives its own type,
# which later stores convert into (the `first_*` sections). The sibling arms
# of one `if` / `match` / `try` bind a local together, whichever comes first
# (`arm_*`). The
# typed values the pending sections store come from `w64` / `w32`.
from __future__ import annotations
from enum import IntEnum
from typing import Callable, Iterator
from tpy import (int8, int16, int32, int64, uint8, float32, Fn, Own,
                 dispatch, error_return, ReturnException)
import asyncio


class Failed(Exception, ReturnException):
    pass


class Ctx:
    def __enter__(self) -> Ctx:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def big() -> int:
    return 10 ** 20


def w64(v: int64) -> int64:
    return v


def w32(v: int32) -> int32:
    return v


def f8() -> int8:
    return 5


def g8() -> int8:
    return 6


def take64(v: int64) -> int64:
    return v + 1


def apply(f: Callable[[int32], int32], v: int32) -> int32:
    return f(v)


class Box:
    total: int64

    # constructor: the second pass reads the int64 stored by the first
    def __init__(self) -> None:
        self.total = 0
        i = 1  # tpyc: type(int64)
        for _ in range(2):
            self.total = i + i  # tpyc: ok
            print("ctor", self.total)
            i = w64(3_000_000_000)
        # constructor: an int8 value stored into a literal-seeded local
        # converts into its int32
        w = 100  # tpyc: type(int32)
        y = w * 3
        w = int8(3)
        print("first_ctor", y, w)

    # method: the same read-before-store shape
    def run(self) -> None:
        i = 1  # tpyc: type(int64)
        for _ in range(2):
            print("method", i + i)
            i = w64(3_000_000_000)

    # method: the same int8 store into a literal-seeded local
    def first_method(self) -> None:
        x = 100  # tpyc: type(int32)
        y = x * 3
        x = int8(3)
        print("first_method", y, x)


# free function: the loop's second pass reads the int64 stored by the first,
# a value no int32 holds
def free_fn() -> None:
    i = 1  # tpyc: type(int64)
    for _ in range(2):
        print("free", i + i)
        i = w64(3_000_000_000)


# generator body: yields read the local before its typed store
def gen() -> Iterator[int64]:
    i = 1  # tpyc: type(int64)
    for _ in range(2):
        yield i + i  # tpyc: ok
        i = w64(3_000_000_000)


# async body: the typed store follows a suspension
async def async_fn() -> int64:
    i = 1  # tpyc: type(int64)
    total = 0  # tpyc: type(int64)
    for _ in range(2):
        total += i + i
        await asyncio.sleep(0)
        i = w64(3_000_000_000)
    return total


# comprehension: a read there needs the type now, after every store
def comp(xs: list[int32]) -> Own[list[int64]]:
    s = 0  # tpyc: type(int64)
    s = w64(5000000000)
    return [s + x for x in xs]


# closure: a nested def reads the local after its stores; a nested def storing
# into a local through `nonlocal` fixes its type first, a derived one too;
# a name a nested def, a lambda or a generator expression binds itself only
# shadows the enclosing literal-seeded local, which a later wider store
# still widens
def closure() -> int64:
    n = 0  # tpyc: type(int64)
    n = w64(7)

    def inner() -> int64:
        return n * 2
    k = 0  # tpyc: type(int32)
    k = w32(3)
    j = k  # tpyc: type(int32)

    def store() -> None:
        nonlocal j
        j = 4
    store()
    i = 0  # tpyc: type(int64)
    t = sum(i for i in [1, 2])
    u = apply(lambda i: i + 1, 2)

    def shadow(i: int64) -> int64:
        return i
    i = w64(5_000_000_000)
    print("closure", j, t, u, shadow(i), i)
    return inner()


# context-manager body
def cm_body() -> None:
    with Ctx():
        i = 1  # tpyc: type(int64)
        for _ in range(2):
            print("cm", i + i)
            i = w64(3_000_000_000)


# try/finally: the read in `finally` sees the widened local
def try_finally() -> None:
    i = 1  # tpyc: type(int64)
    try:
        for _ in range(2):
            print("try", i + i)
            i = w64(3_000_000_000)
    finally:
        print("finally", i)


# @error_return body
@error_return(Failed)
def er_body(fail: bool) -> int64:
    i = 1  # tpyc: type(int64)
    for _ in range(2):
        if fail:
            raise Failed()
        print("er", i + i)
        i = w64(3_000_000_000)
    return i


# match arm
def match_arm(k: int32) -> None:
    match k:
        case 0:
            i = 1  # tpyc: type(int64)
            for _ in range(2):
                print("match", i + i)
                i = w64(3_000_000_000)
        case _:
            print("match other")


# true division over two fixed widths, an IntEnum operand beside an int64,
# and a narrower value converting into a local its first store typed int64
class Level(IntEnum):
    LOW = 1


def mixed_widths(a: int64, b: int32) -> None:
    x = a  # tpyc: type(int64)
    x = b
    print("mixed", a / b, Level.LOW + a, x)


# a slice-assign bound over a local an operation made an int: `+= 10 ** 20`
# stores an `int`, the operator's result for a literal no int32 holds
def slice_bound(xs: list[int32]) -> None:
    n = 1  # tpyc: type(int)
    n += 10 ** 20  # tpyc: ok
    xs[n - 10 ** 20:] = [0]
    print("slice", xs, n)


# accumulator over int values: `s` is an int, as in CPython
def accumulate(xs: list[int]) -> int:
    s = 0  # tpyc: type(int)
    for x in xs:
        s += x
    return s


# derived local: `j` is first bound from a pending value and follows it
def derived() -> None:
    steps = 0  # tpyc: type(int64)
    j = steps + 1  # tpyc: type(int64)
    steps = w64(3000000000)
    print("derived", steps, j)


# a uint8 value joins the default int. Only `n` is printed: the CPython
# stub keeps the stored value's uint8 class for `fixed OP int`, so `n - 300`
# would differ there -- a limit of the stub, not of the rule.
def unsigned_store(b: uint8) -> None:
    n = 0  # tpyc: type(int32)
    n = b
    print("uint8", n)


# a negative literal seed
def negative_seed() -> None:
    lo = -1  # tpyc: type(int64)
    print("negative", lo * 2)
    lo = w64(-5000000000)
    print("negative", lo)


# a literal beyond int32 makes the local an int
def wide_literal() -> None:
    w = 0  # tpyc: type(int)
    print("wide", w + 1)
    w = 3000000000  # tpyc: warning(/outside default int32 range; promoting variable to int/)
    print("wide", w * 4)


# a literal no int32 holds is an `int` operand whatever the other side
# settles to, left, right or as a shift's receiver, so the operator the
# settle picks is the one the operation was typed with
def wide_literal_operand() -> None:
    p = 1  # tpyc: type(int64)
    for _ in range(2):
        q = 3000000000 + p  # tpyc: type(int)
        s = p - 3000000000  # tpyc: type(int)
        t = 3000000000 >> p  # tpyc: type(int)
        print("wide_operand", q, s, t)
        p = w64(2)


# Two candidates of one arity whose lambda parameter types differ; the
# lambda's result type `U` is decided by a trial of its body. Both return the
# same value, since CPython's dispatch stub takes the first by arity alone.
@dispatch
def apply_one[T, U](f: Fn[[str], U], x: T) -> int32:
    return 1


@dispatch
def apply_one[T, U](f: Fn[[int32], U], x: T) -> int32:
    return 1


# overload trial: the str candidate's trial reads the captured local, which
# settles it, then fails on `str + int32`; the operations deferred before the
# call (the store into `t`, the sum `q`) resolve once, after the call
def overload_trial() -> None:
    p = 1  # tpyc: type(int32)
    t: int32 = 7
    t = p
    q = p + 3_000_000_000  # tpyc: type(int)
    print("overload_trial", apply_one(lambda a: a + p, 0), t, q)
    p = w32(2)
    print("overload_trial", p)


# the deferred uses: a declared parameter, an index, an f-string, print,
# return, and an operator against a declared int32
def uses(xs: list[int32], a: int32) -> int:
    k = 0  # tpyc: type(int64)
    print("param", take64(k))
    print(f"fstring {k}")
    k = w64(1)
    j = 0  # tpyc: type(int)
    print("index", xs[j])
    j = big()
    p = 0  # tpyc: type(int)
    r = p + a  # tpyc: type(int)
    p = big()
    print("sum", r, p + a)
    return p


# `+=` into a declared int32 slot takes the value at its settled type (int)
# through the checked narrow a declared slot applies to an int value
def declared_aug() -> None:
    p = 0  # tpyc: type(int)
    q: int32 = 7
    q += p + 1  # tpyc: ok
    print("declared_aug", q)
    p = big()
    print("declared_aug", p)


# a value as first binding gives the local its type, int and float alike: a
# bare literal stored later converts into it, as does a narrower value; an
# augmented assignment stores an operation's value, in which the literal
# adapts
def float_literal(a16: int16, a32: int32, f32: float32) -> None:
    b = a16  # tpyc: type(int16)
    b = 10
    c = a16  # tpyc: type(int16)
    c += 10
    # 0.5 is exact in float32, so the output matches CPython's double; a
    # literal that rounds is float/float32_declared_literal_rounds
    f = f32  # tpyc: type(float32)
    f = 0.5
    g = f32  # tpyc: type(float32)
    g *= 0.1
    h = a32  # tpyc: type(int32)
    h = 10
    h = a16
    k = a16  # tpyc: type(int16)
    k = 10 + 5
    print("literal_family", b * 50, c, f * 3, g, h * 5000, k * 50)


def w16(v: int16) -> int16:
    return v


# a numeric type constructor is a value like any other: as the first binding
# it gives the local its type, and a later literal converts into it; as a
# later store into a literal-seeded local it is a value that widens nothing
# narrower than the default
def first_declares() -> None:
    x = int8(3)  # tpyc: type(int8)
    x = 10
    y = 10  # tpyc: type(int32)
    y = int8(3)
    f = float32(1.5)  # tpyc: type(float32)
    f = 0.5
    print("first_declares", x, y, f)


# a use before a later constructor store computes at the type the first
# binding gave, here the default int32: 300 is no int8
def first_use_before() -> None:
    x = 100  # tpyc: type(int32)
    print("first_use_before", x * 3)
    x = int8(3)
    print("first_use_before", x)


# a typed value first, then a constructor: the narrower int8 converts into the
# int16 the first binding gave
def first_value(y: int16) -> None:
    x = w16(y)  # tpyc: type(int16)
    x = int8(3)
    print("first_value", x)


# the sibling arms of one `if` bind a local together, whichever is read
# first: a literal arm beside a wider typed arm takes the typed arm's type
# (int64, int, float), and typed arms join (int8 and int16 give int16). The
# int64 product shows the width: 3000000000 is no int32. The same annotation
# in both arms is one first binding, not a later annotation
def arm_join(c: bool, v: int64, wide: float) -> None:
    if c:
        a = int64(3)  # tpyc: type(int64)
    else:
        a = 3  # tpyc: type(int64)
    if c:
        b = 3  # tpyc: type(int64)
    else:
        b = v  # tpyc: type(int64)
    if c:
        n = 0  # tpyc: type(int)
    else:
        n = big()  # tpyc: type(int)
    if c:
        m = big()  # tpyc: type(int)
    else:
        m = 0  # tpyc: type(int)
    if c:
        p = int8(3)  # tpyc: type(int16)
    else:
        p = int16(4)  # tpyc: type(int16)
    if c:
        q = int16(4)  # tpyc: type(int16)
    else:
        q = int8(3)  # tpyc: type(int16)
    if c:
        f = wide  # tpyc: type(float)
    else:
        f = 0.5  # tpyc: type(float)
    if c:
        r: int16 = 1  # tpyc: ok
    else:
        r: int16 = 2  # tpyc: ok
    print("arm_join", a * 1_000_000_000, b * 1_000_000_000, n, m, p, q, f, r)


# method: a literal arm beside an int64 arm takes int64
class ArmBox:
    def arm_method(self, c: bool, v: int64) -> None:
        if c:
            x = 3  # tpyc: type(int64)
        else:
            x = v  # tpyc: type(int64)
        print("arm_method", x * 1_000_000_000)


# generator: the same arms in a generator body
def arm_gen(c: bool, v: int64) -> Iterator[int64]:
    if c:
        x = v  # tpyc: type(int64)
    else:
        x = 3  # tpyc: type(int64)
    yield x * 1_000_000_000


# match: three arms bind one local -- int8, int64 and a literal join to int64
def arm_match(k: int32, v: int64) -> None:
    match k:
        case 0:
            x = int8(3)  # tpyc: type(int64)
        case 1:
            x = v  # tpyc: type(int64)
        case _:
            x = 3  # tpyc: type(int64)
    # CPython's stub keeps the int8 arm's value an int8, whose product wraps
    print("arm_match", x if k == 0 else x * 1_000_000_000)


def risky(fail: bool) -> None:
    if fail:
        raise ValueError("risky")


# try: the body binds first, so its int16 is the type and the handler's int8
# converts into it
def arm_try(fail: bool) -> None:
    try:
        x = int16(4)  # tpyc: type(int16)
        risky(fail)
    except ValueError:
        x = int8(3)  # tpyc: type(int16)
    print("arm_try", x)


# an accumulator first bound int16 takes the int8 values added to it
def first_accum(data: list[int8]) -> None:
    count = int16(0)  # tpyc: type(int16)
    for b in data:
        count += b
    print("first_accum", count)


# an annotation declares too, and a constructor stored later is a value that
# converts into it
def first_annotation() -> None:
    x: int16 = 0  # tpyc: type(int16)
    x = int8(3)
    print("first_annotation", x)


# the literal and value rules without constructors
def first_unchanged(data: list[uint8]) -> None:
    count = 0  # tpyc: type(int32)
    for b in data:
        count += b
    total = 0  # tpyc: type(int64)
    total += w64(7)
    v = f8()  # tpyc: type(int8)
    v = g8()
    print("first_unchanged", count, total, v)


# a wider float value narrows into a float32 local only when the store writes
# the conversion (2.5 is exact in float32); the bare store is refused
# (float/error_width_wider_value)
def first_float_explicit(wide: float) -> None:
    x = float32(1.5)  # tpyc: type(float32)
    x = float32(wide)  # tpyc: ok
    print("first_float_explicit", x)


# generator body: an int8 store into a literal-seeded local converts into its
# int32
def first_gen() -> Iterator[int32]:
    x = 100  # tpyc: type(int32)
    yield x * 3
    x = int8(3)
    yield x


# async body: the int8 store follows a suspension
async def first_async() -> int32:
    x = 100  # tpyc: type(int32)
    await asyncio.sleep(0)
    y = x * 3
    x = int8(3)
    print("first_async", x)
    return y


# comprehension: an element reads the literal-seeded local
def first_comp(xs: list[int8]) -> None:
    x = 100  # tpyc: type(int32)
    print("first_comp", [x * v for v in xs])
    x = int8(3)
    print("first_comp", x)


# closure: a nested def defined before the int8 store reads the local
def first_closure() -> None:
    x = 100  # tpyc: type(int32)

    def times3() -> int32:
        return x * 3
    print("first_closure", times3())
    x = int8(3)
    print("first_closure", times3())


# context-manager body
def first_with() -> None:
    with Ctx():
        x = 100  # tpyc: type(int32)
        y = x * 3
        x = int8(3)
        print("first_with", y, x)


# try/finally: the int8 store stands in `finally`
def first_try() -> None:
    x = 100  # tpyc: type(int32)
    try:
        print("first_try", x * 3)
    finally:
        x = int8(3)
        print("first_finally", x)


# @error_return body
@error_return(Failed)
def first_er(fail: bool) -> int32:
    x = 100  # tpyc: type(int32)
    if fail:
        raise Failed()
    y = x * 3
    x = int8(3)
    print("first_er", x)
    return y


# match arm
def first_match(k: int32) -> None:
    match k:
        case 0:
            x = 100  # tpyc: type(int32)
            y = x * 3
            x = int8(3)
            print("first_match", y, x)
        case _:
            print("first_match other")


# yield of a pending local
def gen_yield() -> Iterator[int64]:
    v = 0  # tpyc: type(int64)
    yield v
    v = w64(9000000000)
    yield v


# literal-only: the default int, as always
def literal_only() -> None:
    c = 0  # tpyc: type(int32)
    c = 5
    c += 1
    print("literal", c)


def main() -> None:
    free_fn()
    b = Box()
    b.run()
    b.first_method()
    print("gen", list(gen()))
    print("async", asyncio.run(async_fn()))
    print("comp", comp([1, 2]))
    c = closure()
    print("closure", c)
    cm_body()
    try_finally()
    # bound first: a print argument that prints interleaves
    # (BUGS.md#print-arg-output-interleaves)
    try:
        v = er_body(False)
        print("er", v)
    except Failed:
        print("er failed")
    match_arm(0)
    print("acc", accumulate([1, 2, 10 ** 20]))
    derived()
    unsigned_store(250)
    negative_seed()
    wide_literal()
    wide_literal_operand()
    overload_trial()
    u = uses([10, 20], 5)
    print("uses", u)
    declared_aug()
    # float32 for CPython: its stub computes `g *= 0.1` in float32 only on a
    # float32 value
    float_literal(15, 7, float32(1.5))
    first_declares()
    first_use_before()
    first_value(4)
    arm_join(True, 7, 1.5)
    arm_join(False, 7, 1.5)
    ArmBox().arm_method(True, 7)
    ArmBox().arm_method(False, 7)
    print("arm_gen", list(arm_gen(True, 7)), list(arm_gen(False, 7)))
    arm_match(0, 7)
    arm_match(1, 7)
    arm_match(2, 7)
    arm_try(False)
    arm_try(True)
    first_accum([1, 2])
    first_annotation()
    first_unchanged([200, 100])
    first_float_explicit(2.5)
    print("first_gen", list(first_gen()))
    va = asyncio.run(first_async())
    print("first_async", va)
    first_comp([1, 2])
    first_closure()
    first_with()
    first_try()
    try:
        v8 = first_er(False)
        print("first_er", v8)
    except Failed:
        print("first_er failed")
    first_match(0)
    print("gen_yield", list(gen_yield()))
    literal_only()
    mixed_widths(3_000_000_000, 2)
    slice_bound([1, 2, 3])


main()
