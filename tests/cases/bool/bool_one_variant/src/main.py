# bool(x) has one conversion variant: a generic body and the direct call render
# the same ::tpy::__bool__ call and print the same truth value for every family.
from typing import Callable
from tpy import Truthy, int8, int16, int32, int64, uint8, uint16, uint32, uint64
from tpy import float32, char, String, StrView, readonly
import part_more


class Flag:
    def __init__(self, on: bool) -> None:
        self.on = on

    def __bool__(self) -> bool:
        return self.on


class Bag:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __len__(self) -> int32:
        return self.n


def gb[T: Truthy](x: T) -> bool:
    # The generic twin of every direct bool(x) below.
    return bool(x)  # tpyc: ok


def apply(f: Callable[[int32], bool], v: int32) -> bool:
    return f(v)


def families() -> None:
    # generic body vs direct call, per builtin family
    i8 = int8(0)
    i16 = int16(-3)
    i32 = 0  # tpyc: type(int32)
    i64 = int64(5)
    u8 = uint8(0)
    u16 = uint16(9)
    u32 = uint32(0)
    u64 = uint64(1)
    print("fixed", gb(i8), bool(i8), gb(i16), bool(i16), gb(i32), bool(i32))  # tpyc: ok
    print("fixed", gb(i64), bool(i64), gb(u8), bool(u8), gb(u16), bool(u16))
    print("fixed", gb(u32), bool(u32), gb(u64), bool(u64))
    zero: int = 0
    big: int = 10 ** 30
    neg: int = -5
    print("bigint", gb(zero), bool(zero), gb(big), bool(big), gb(neg), bool(neg))  # tpyc: ok
    f = 0.0
    nz = -0.0
    nan = float("nan")
    print("float", gb(f), bool(f), gb(nz), bool(nz), gb(nan), bool(nan))  # tpyc: ok
    f32 = float32(0.0)
    print("float32", gb(f32), bool(f32), bool(float32(2.5)))
    t = True
    print("bool", gb(t), bool(t), bool(False), bool())
    s = ""
    sv: StrView = "v"
    st = String("")
    print("strs", gb(s), bool(s), gb(sv), bool(sv), gb(st), bool(st))  # tpyc: ok
    # a char is a one-character str: always true, through its __len__
    c = char("a")
    print("char", bool(c))  # tpyc: ok
    # a NUL char is still a one-character str: true
    nul = char("\0")
    text = "a\0"
    print("char nul", bool(nul), bool(text[1]))  # tpyc: ok


def first(xs: readonly[list[bool]]) -> bool:
    # a const list[bool] element: libc++'s const bit proxy is a class
    return bool(xs[0])  # tpyc: ok


def loop_truth(xs: readonly[list[bool]]) -> str:
    # a loop variable over a const list[bool]
    out = ""
    for b in xs:
        out += str(bool(b)) + str(gb(b))  # tpyc: ok
    return out


def bool_elements() -> None:
    # a list[bool] element is a bit proxy in C++; bool() reads its bit
    xs = [False, True]
    xs.append(False)
    print("listbool", bool(xs[0]), bool(xs[1]), gb(xs[1]))  # tpyc: ok
    print("listbool", first(xs), first([True]))
    for b in xs:
        print("listbool loop", bool(b), gb(b))  # tpyc: ok
    print("listbool loop", loop_truth(xs))
    # the bit proxy's truth value follows a later write to the element
    xs[0] = True
    print("listbool write", bool(xs[0]))


def user_classes() -> None:
    # __bool__ through the Truthy variant, __len__ through the len fallback
    print("user", gb(Flag(False)), bool(Flag(True)), bool(Bag(0)), bool(Bag(2)))
    on = Flag(False)
    on.on = True
    print("user", gb(on), bool(on))


def literals() -> None:
    # literal arguments settle at the parameter like any value
    print("lit", bool(0), bool(7), bool(-1), bool(0.0), bool(""), bool("x"))
    # a wide literal at the Truthy parameter is passed as int (BigInt)
    print("lit", bool(5000000000))  # tpyc: warning(/passed as int \(BigInt\)/)


def callables() -> None:
    # bool as a callable value fits a typed Callable slot
    xs = [0, 3, 0, 4]
    print("filter", list(filter(bool, xs)))
    print("map", list(map(bool, xs)))
    print("slot", apply(bool, 0), apply(bool, 8))
    names = ["", "a"]
    print("map str", list(map(bool, names)))


def main() -> None:
    families()
    bool_elements()
    user_classes()
    literals()
    callables()
    part_more.main()


main()
