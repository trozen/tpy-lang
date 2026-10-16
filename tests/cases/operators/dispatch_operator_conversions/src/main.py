# An operator on a record answers like its explicit dunder call, operand
# conversions and the reflected comparison included (variants winner-first).
import asyncio
from datetime import timedelta
from typing import Any, Iterator, Literal, cast
from tpy import dispatch, int8, int16, int32, int64, float32, Own


class B:
    def __init__(self) -> None:
        pass

    @dispatch
    def __mul__(self, x: int) -> int32:
        return 1

    @dispatch
    def __mul__(self, x: float) -> int32:
        return 2

    @dispatch
    def __rmul__(self, x: int) -> int32:
        return 11

    @dispatch
    def __rmul__(self, x: float) -> int32:
        return 12


class BN:
    def __init__(self) -> None:
        pass

    @dispatch
    def mul(self, x: int) -> int32:
        return 1

    @dispatch
    def mul(self, x: float) -> int32:
        return 2


class W:
    def __init__(self) -> None:
        pass

    @dispatch
    def __mul__(self, x: int16) -> int32:
        return 16

    @dispatch
    def __mul__(self, x: int32) -> int32:
        return 32


class VI:
    log: str

    def __init__(self) -> None:
        self.log = ""

    @dispatch
    def __iadd__(self, x: int) -> "VI":
        self.log = self.log + "I"
        return self

    @dispatch
    def __iadd__(self, x: float) -> "VI":
        self.log = self.log + "F"
        return self


class V:
    log: str

    def __init__(self) -> None:
        self.log = ""

    @dispatch
    def __iadd__(self, x: int) -> "V":
        self.log = self.log + "iadd"
        return self

    @dispatch
    def __iadd__(self, x: float) -> "V":
        self.log = self.log + "iaddF"
        return self

    def __add__(self, x: int32) -> Own["V"]:
        r = V()
        r.log = self.log + "add"
        return r


class C:
    def __init__(self) -> None:
        pass

    @dispatch
    def __eq__(self, x: int) -> bool:
        return True

    @dispatch
    def __eq__(self, x: float) -> bool:
        return False

    @dispatch
    def __lt__(self, x: int) -> bool:
        return True

    @dispatch
    def __lt__(self, x: float) -> bool:
        return False

    @dispatch
    def __gt__(self, x: int) -> bool:
        return True

    @dispatch
    def __gt__(self, x: float) -> bool:
        return False


class G:
    def __init__(self) -> None:
        pass

    def __gt__(self, x: int32) -> bool:
        return True


class CF:
    def __init__(self) -> None:
        pass

    def __gt__(self, x: float) -> bool:
        return x > 1.5


class A0:
    def __init__(self) -> None:
        pass


class GA:
    def __init__(self) -> None:
        pass

    def __gt__(self, x: A0) -> bool:
        return True


class NeR:
    def __init__(self) -> None:
        pass

    def __ne__(self, other: "NeL") -> bool:
        return True


class NeL:
    def __init__(self) -> None:
        pass

    def __eq__(self, other: NeR) -> bool:
        return True


class S64:
    def __init__(self) -> None:
        pass

    def __mul__(self, x: int64) -> int64:
        return x * 2


class S8:
    def __init__(self) -> None:
        pass

    def __mul__(self, x: int8) -> int32:
        return int32(x) * 3


class Ops:
    def __init__(self) -> None:
        pass

    @dispatch
    def __floordiv__(self, x: int) -> int32:
        return 1

    @dispatch
    def __floordiv__(self, x: float) -> int32:
        return 2

    @dispatch
    def __rfloordiv__(self, x: int) -> int32:
        return 3

    @dispatch
    def __rfloordiv__(self, x: float) -> int32:
        return 4

    @dispatch
    def __truediv__(self, x: int) -> int32:
        return 5

    @dispatch
    def __truediv__(self, x: float) -> int32:
        return 6

    @dispatch
    def __rtruediv__(self, x: int) -> int32:
        return 7

    @dispatch
    def __rtruediv__(self, x: float) -> int32:
        return 8

    @dispatch
    def __mod__(self, x: int) -> int32:
        return 9

    @dispatch
    def __mod__(self, x: float) -> int32:
        return 10

    @dispatch
    def __and__(self, x: int) -> int32:
        return 11

    @dispatch
    def __and__(self, x: float) -> int32:
        return 12

    @dispatch
    def __ror__(self, x: int) -> int32:
        return 13

    @dispatch
    def __ror__(self, x: float) -> int32:
        return 14

    @dispatch
    def __xor__(self, x: int) -> int32:
        return 15

    @dispatch
    def __xor__(self, x: float) -> int32:
        return 16

    @dispatch
    def __lshift__(self, x: int) -> int32:
        return 17

    @dispatch
    def __lshift__(self, x: float) -> int32:
        return 18

    @dispatch
    def __rrshift__(self, x: int) -> int32:
        return 19

    @dispatch
    def __rrshift__(self, x: float) -> int32:
        return 20


class Pt:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Pick:
    own: Pt

    def __init__(self) -> None:
        self.own = Pt(0)

    @dispatch
    def __or__(self, p: Pt) -> Pt:
        return p

    @dispatch
    def __or__(self, x: float) -> Pt:
        return self.own


class Base:
    def __init__(self) -> None:
        pass

    def __add__(self, x: int) -> int32:
        return 7

    def __contains__(self, x: int) -> bool:
        return x > 0

    def __iadd__(self, x: int) -> "Base":
        print("inherited_iadd:", x)
        return self

    def __neg__(self) -> int32:
        return -3


class Child(Base):
    def __init__(self) -> None:
        super().__init__()


class GBase[T]:
    def __init__(self) -> None:
        pass

    def __add__(self, x: T) -> int32:
        return 9

    def __contains__(self, x: T) -> bool:
        return True


class D(GBase[int]):
    def __init__(self) -> None:
        super().__init__()


class Holder:
    b: B

    def __init__(self) -> None:
        self.b = B()

    def scaled(self, n: int32) -> int32:
        # Method body: a field receiver converts the operand the same way.
        return self.b * n  # tpyc: ok


trace: list[str] = []


def tick(s: str, n: int32) -> int32:
    trace.append(s)
    return n


def any_mid(s: str) -> Any:
    trace.append(s)
    x: int32 = 2
    return x


def make_c(s: str) -> Own[C]:
    trace.append(s)
    return C()


def lit34(k: Literal[3, 4], b: B) -> None:
    # literal_value: a `Literal[...]` value converts as its storage type
    # does, in every spelling (CPython runs the `int` variants).
    print("literal_value:", b * k, b.__mul__(k), BN().mul(k))  # tpyc: ok


def lit_slots(k: Literal[3, 4]) -> None:
    # literal_slots: a single variant at `int64` widens the value; at `int8`
    # it narrows, as every value of the set fits.
    s64 = S64()
    s8 = S8()
    print("literal_slots:", s64 * k, s8 * k, s8.__mul__(k))  # tpyc: ok


async def scaled_later(o: Ops, n: int32) -> int32:
    await asyncio.sleep(0)
    # Async body: the operand converts inside the resumable frame.
    return o // n  # tpyc: ok


def gen(c: C, xs: list[int32]) -> Iterator[bool]:
    for x in xs:
        # Generator: the comparison converts inside the resumable frame.
        yield c == x  # tpyc: ok


def main() -> None:
    n32: int32 = 5
    n8: int8 = 5
    f32: float32 = 2.5
    m: int32 = 2
    b = B()
    w = W()
    c = C()
    g = G()
    # arith: forward and reflected sets convert the operand (CPython: 1 1 2 16).
    print("arith:", b * n32, b * n8, b * f32, w * n8)  # tpyc: ok
    print("arith:", n32 * b, f32 * b)  # tpyc: ok
    # literal: a literal operand takes the call's literal pin.
    print("literal:", b * 3, 3 * b)  # tpyc: ok
    lit34(3, b)
    vi = VI()
    # inplace: `__iadd__` converts its operand.
    vi += n32  # tpyc: ok
    vi += f32  # tpyc: ok
    v = V()
    # inplace: `__iadd__` answers before the `__add__` fallback, as in CPython.
    v += n32  # tpyc: ok
    print("inplace:", vi.log, v.log)
    # compare: forward comparisons convert (CPython runs the `int` variants).
    eq = c == m  # tpyc: ok
    lt = c < m  # tpyc: ok
    ne = c != m  # tpyc: ok
    print("compare:", eq, lt, ne)
    # reflected: the record on the right answers with its reflected dunder.
    r_eq = 3 == c  # tpyc: ok
    r_ne = 3 != c  # tpyc: ok
    r_meq = m == c  # tpyc: ok
    r_lt = 3 < c  # tpyc: ok
    r_mlt = m < c  # tpyc: ok
    r_g = 3 < g  # tpyc: ok
    print("reflected:", r_eq, r_ne, r_meq, r_lt, r_mlt, r_g)
    # chained: the middle record operand is evaluated once.
    chained = 1 < make_c("mid") < 3  # tpyc: ok
    print("chained:", chained, trace)
    trace.clear()
    # chained_conv: the middle operand converts to `int` for `C.__lt__` and
    # to `float` for `CF.__gt__`, and is evaluated once.
    cf = CF()
    chained_conv = c < tick("mid", 2) < cf  # tpyc: ok
    print("chained_conv:", chained_conv, trace)
    trace.clear()
    # chained_cast: the checked `cast` from `Any` runs once, in the temp's
    # init; each pair converts the temp for its own comparison.
    chained_cast = 0 < cast(int32, any_mid("cast")) < 3  # tpyc: ok
    chained_cast_conv = c < cast(int32, any_mid("conv")) < cf  # tpyc: ok
    print("chained_cast:", chained_cast, chained_cast_conv, trace)
    trace.clear()
    # chained_bytes: a bytes literal operand renders as a static span for
    # its comparison and is still bound once.
    lo = bytes(b"a")
    hi = bytes(b"z")
    print("chained_bytes:", lo < b"m" < hi, b"a" < lo + b"1" < b"z")  # tpyc: ok
    # ne_order: `!=` asks the left operand's negated `__eq__` before the
    # right one's reflected `__ne__`, as CPython does.
    nl = NeL()
    nr = NeR()
    print("ne_order:", nl != nr)  # tpyc: ok
    # record_reflected: a record without `__lt__` compared with a record
    # whose `__gt__` takes it.
    a0 = A0()
    ga = GA()
    print("record_reflected:", a0 < ga)  # tpyc: ok
    d = timedelta(seconds=10)
    k: int32 = 4
    # timedelta: a library record converts an `int32` / `float32` operand.
    print("timedelta:", d * k, k * d, d / k, d // k, d * f32)  # tpyc: ok
    # method: a field receiver inside a method.
    print("method:", Holder().scaled(n32))
    # comprehension: a filter comparing a record with an `int32`.
    hits = [x for x in [1, 2, 3] if c < x]  # tpyc: ok
    print("comprehension:", hits)
    print("generator:", list(gen(c, [1, 2])))
    # closure: the operator converts inside a nested function.
    def scale(x: int32) -> int32:
        return b * x  # tpyc: ok
    print("closure:", scale(n32))
    i: int32 = 0
    # while: a reflected comparison as the loop condition.
    while i < g and i < 2:  # tpyc: ok
        i += 1
    print("while:", i)
    p = Pt(1)
    pick = Pick()
    # reference: the dunder hands back its record operand, an alias of `p`,
    # so a write to `p` afterwards shows through it (no copy).
    got = pick | p  # tpyc: ok
    p.x += 10  # tpyc: ok
    print("reference:", got.x, p.x)
    ch = Child()
    # inherited: an operator and a membership test find the parent's dunder.
    print("inherited:", ch + n32, n32 in ch, 0 in ch)  # tpyc: ok
    # inherited_inplace: `+=` and unary `-` find the parent's dunders.
    ch += n32  # tpyc: ok
    print("inherited_unary:", -ch)  # tpyc: ok
    dd = D()
    # generic_parent: the parent's `T` is the child's `int`.
    print("generic_parent:", dd + n32, n32 in dd)  # tpyc: ok
    lit_slots(4)
    o = Ops()
    f = 1.5
    # division / bitwise / shift: overloaded sets convert, forward and
    # reflected.
    print("division:", o // n32, n32 // o, o / n32, n32 / o, o // f, f / o,  # tpyc: ok
          o % n32)  # tpyc: ok
    print("bitwise:", o & n32, n32 | o, o ^ n32, o & f, f | o)  # tpyc: ok
    print("shift:", o << n32, n32 >> o, o << f, f >> o)  # tpyc: ok
    print("async:", asyncio.run(scaled_later(o, n32)))
    try:
        # try_body: a comparison inside `try`, its sibling in `finally`.
        print("try_body:", c == n32)  # tpyc: ok
    finally:
        print("finally:", c != n32)  # tpyc: ok
    match n32:
        # match_guard: a reflected comparison as a guard.
        case 5 if 3 < c:  # tpyc: ok
            print("match_guard: hit")
        case _:
            print("match_guard: miss")


main()
# module: a module-level statement converts the operand too.
mod_o = Ops()
mod_n: int32 = 4
print("module:", mod_o // mod_n, mod_n // mod_o)  # tpyc: ok
