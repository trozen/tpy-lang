# int_const_fold_sinks at --default-int=BigInt, less the literal slice bounds
# (BUGS.md#bigint-default-literal-slice-bound-rejects) and the enum lookup
# (BUGS.md#intenum-lookup-bigint-default-rejects), which reject at this width
# for the hand literal too.
from enum import IntEnum
from typing import Callable, Iterator

from tpy import int32, int64, uint8, uint32, uint64


class Counter:
    def __init__(self) -> None:
        pass

    def bump(self, k: int) -> int:
        return k


class Holder:
    k: int
    w: int64

    def __init__(self, k: int) -> None:
        self.k = k
        self.w = 2**40  # the folded value at an int64 field


def take_int(v: int) -> int:
    return v


def take_i64(v: int64) -> int64:
    return v


def ret_wide() -> int:
    return 2**40  # the folded value at a return


def gen_wide() -> Iterator[int]:
    yield 2**40  # the folded value at a yield


G = 2**40  # module-level binding of a folded value


def sinks() -> None:
    c = Counter()
    # Method / free function / constructor arguments.
    print("meth", c.bump((2**30 * 4) // 4))
    print("free", take_int(2**40), take_i64(2**40))
    print("ctor", Holder(2**40).k, Holder(1).w)
    # Annotated and inferred locals.
    a: int = 2**40
    b = 2**40  # tpyc: type(int)
    print("local", a, b)
    print("ret", ret_wide())
    xs: list[int] = [2**40, 1 << 33]
    d: dict[str, int] = {"k": 2**40}
    print("elem", xs, d["k"])
    print("global", G)
    print("gen", list(gen_wide()))
    f: Callable[[], int] = lambda: 2**40
    print("lambda", f())
    print("builtin", bin(2**62 + 1))


def formatting() -> None:
    # An f-string formats a folded value as it formats the hand literal.
    print("fstr", f"{-2**63}", f"{2**40}", f"{1099511627776}", f"{2 * 3}")  # tpyc: ok
    print("fstr", f"{2**100}", f"{2**64 - 1}", f"{2 * 3!r}")  # tpyc: ok
    print("str", str(2**40), str(-2**63))


def unary() -> None:
    # Negation and inversion of a folded value past int64.
    print("neg", -(-2**63), ~(2**63), -(2**40))


def operands() -> None:
    n = 5
    print("operand", n + (2**30 * 4) // 4, n * (2 * 3))
    xs = [10, 20, 30, 40]
    print("index", xs[2**0], xs[2**1 - 2])
    xs[2**1] = 99
    print("setitem", xs)
    r = [0] * (2 * 3)
    print("repeat", r)
    for i in range(1, 2 + 1):  # a folded range bound
        print("range", i)


def unsigned() -> None:
    u: uint64 = 5
    # A negative literal compares by value against an unsigned operand.
    print("unsigned", u > -1, u > -(2**63), u == 5, u < 2**64 - 1)  # tpyc: ok
    print("unsigned", -1 < u, -(2**63) < u)  # tpyc: ok
    # Below int64 the literal is a BigInt and compares as one.
    print("unsigned", u > -(2**64), -(2**64) < u, u > -(2**63) - 1)  # tpyc: ok
    w: uint64 = 2**64 - 1
    print("unsigned", w == -1, w != -1, -1 < w)  # tpyc: ok
    i: int64 = -1
    # A literal past int64 renders as uint64; the signed side may be negative.
    print("unsigned", 2**63 > i, i < 2**63, 2**64 - 1 == -1)  # tpyc: ok
    a: uint8 = 3
    c: uint32 = 7
    # A literal whose answer the operand's range decides compares through
    # `::std::cmp_*`, no BigInt; one inside the range compares plainly.
    print("narrow", a < 2**63, 2**63 > a, a >= 0, a > 255, a < 200, c < 2**40)  # tpyc: ok
    o: uint8 | None = 3
    if o is not None:
        # A narrowed uint8 side, both operand orders.
        print("narrowed", o > -1, -1 < o, o < 2**63, 2**63 > o)  # tpyc: ok
    q: int64 | None = -1
    p: uint64 | None = 3
    if q is not None and p is not None:
        # A narrowed side that can be negative compares through its inner value.
        print("narrowed", q > 2**63, 2**63 > q, p > -1, -1 < p)  # tpyc: ok


def unproven(q: int64 | None, p: uint64 | None) -> None:
    # An unproven Optional side compares through its checked inner read.
    print("unproven", q < 2**63, 2**63 > q, p > -1, -1 < p)  # tpyc: warning(/Potential None access/)


def union_narrowed(x: int64 | str, y: uint64 | str) -> None:
    # A condition-narrowed union side compares through its member read.
    print("union", not isinstance(x, str) and x > 2**63)  # tpyc: ok
    print("union", not isinstance(y, str) and y > -1)  # tpyc: ok


class Sign(IntEnum):
    NEG = -1


def enum_compare(s: Sign) -> None:
    # An IntEnum compares at its underlying width, so a negative member
    # against a literal past int64 or an unsigned operand compares by value.
    print("enumcmp", 2**63 > s, s < 2**63, 2**64 - 1 == s)  # tpyc: ok
    u: uint64 = 5
    print("enumcmp", u > s, s < u)  # tpyc: ok


def comprehensions() -> None:
    # A literal-valued element widens past the default int as a local does.
    c = [2**40 for _ in range(2)]  # tpyc: type(Array[int, 2])
    print("comp", c, [1099511627776 for _ in range(2)])  # tpyc: ok
    print("setcomp", {2**40 for _ in range(2)})  # tpyc: ok
    print("dictcomp", {k: 2**40 for k in range(2)}, {2**40: k for k in range(1)})  # tpyc: ok
    # An annotated element type that holds the value takes it as is.
    ws: list[int64] = [2**40 for _ in range(2)]  # tpyc: ok
    dv: dict[int32, uint64] = {3: 2**64 - 1 for _ in range(1)}  # tpyc: ok
    print("annotcomp", ws, dv)


def declined() -> None:
    # A zero divisor is not folded: the operation runs and raises.
    try:
        q = 10 // 0
        print("zero", q)
    except ZeroDivisionError:
        print("zero", "ZeroDivisionError")
    # A negative shift count is not folded either.
    try:
        s = 1 << -1
        print("shift", s)
    except ValueError:
        print("shift", "ValueError")
    # Past 4096 bits the value is computed at run time.
    x: int = 2**5000
    print("wide", len(str(x)), x % 1000003)


def main() -> None:
    sinks()
    formatting()
    unary()
    operands()
    unsigned()
    unproven(-1, 3)
    y: uint64 = 3
    union_narrowed(-1, y)
    enum_compare(Sign.NEG)
    comprehensions()
    declined()


main()
