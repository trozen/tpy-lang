# str(x) has one conversion variant: a generic body and the direct call render
# the same ::tpy::__str__ call and print the same text for every builtin family.
from typing import Callable
from tpy import Stringable, int8, int16, uint8, uint16, uint32, int64, uint64
from tpy import float32, char, String, StrView, int32, readonly
import part_subclass


class Point:
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

    def __str__(self) -> str:
        return f"P({self.x}, {self.y})"


def gs[T: Stringable](x: T) -> str:
    # The generic twin of every direct str(x) below.
    return str(x)  # tpyc: ok


def make() -> str:
    return "a string long enough to sit on the heap, not in the SSO buffer"


def make_owned() -> String:
    return String("an owned String long enough to sit on the heap buffer")


def apply(f: Callable[[int32], str], v: int32) -> str:
    return f(v)


def families() -> None:
    # generic body vs direct call, per builtin family
    i8 = int8(-5)
    i16 = int16(-300)
    u8 = uint8(255)
    u16 = uint16(65535)
    u32 = uint32(4000000000)
    i64 = int64(-9000000000)
    u64 = uint64(18446744073709551615)
    n = 7
    print("fixed", gs(i8), str(i8), gs(i16), str(i16), gs(u8), str(u8))
    print("fixed", gs(u16), str(u16), gs(u32), str(u32), gs(i64), str(i64))
    print("fixed", gs(u64), str(u64), gs(n), str(n))
    big: int = 10 ** 30
    print("bigint", gs(big), str(big))
    f = 0.1
    huge = 1e300
    whole = 2.0
    nan = float("nan")
    inf = float("inf")
    print("float", gs(f), str(f), gs(huge), str(huge), gs(whole), str(whole))
    print("float", gs(nan), str(nan), gs(inf), str(inf), str(-inf))
    f32 = float32(0.1)
    print("float32", gs(f32), str(f32))
    b = True
    print("bool", gs(b), str(b), str(False))
    c = char("c")
    print("char", gs(c), str(c))
    s = "hi"
    sv = StrView(s)
    st = String("st")
    print("strs", gs(s), str(s), gs(sv), str(sv), gs(st), str(st))
    print("empty", repr(str()))


def first_text(xs: readonly[list[bool]]) -> str:
    # a const list[bool] element: libc++'s const bit proxy is a class
    return str(xs[0])  # tpyc: ok


def first_repr(xs: readonly[list[bool]]) -> str:
    # repr of a const list[bool] element
    return repr(xs[0])  # tpyc: ok


def loop_text(xs: readonly[list[bool]]) -> str:
    # a loop variable over a const list[bool]
    out = ""
    for b in xs:
        out += str(b) + gs(b)  # tpyc: ok
    return out


def bool_elements() -> None:
    # a list[bool] element is a bit proxy in C++; it still prints True/False
    xs = [True, False]
    xs.append(True)
    print("listbool", str(xs[0]), str(xs[1]), gs(xs[2]))  # tpyc: ok
    print("listbool", first_text(xs), first_text([False]))
    # repr of a mutable element, through a readonly parameter and as `!r`
    print("listbool repr", repr(xs[0]), first_repr(xs), f"{xs[1]!r}")  # tpyc: ok
    # a loop variable over a mutable list[bool], and over a readonly one
    for b in xs:
        print("listbool loop", str(b), gs(b))  # tpyc: ok
    print("listbool loop", loop_text(xs))


def temporaries() -> None:
    # a temporary str / String moves into the result instead of being copied
    t = "x" * 40
    print("temp", str(make()), str(t + t)[:3], str(make_owned())[:9])


def literals() -> None:
    # literal arguments keep their fitting type
    print("lit", str(7), str(-1), str(int8(-5)), str(uint32(4000000000)))
    # a wide literal at the Stringable parameter is passed as int (BigInt)
    print("lit", str(2 ** 70), str(1.5), str(float32(1.5)), str(True), str("lit"))  # tpyc: warning(/passed as int \(BigInt\)/)
    # a spelled fixed width renders at that width
    print("lit", str(int64(-2 ** 63)))  # tpyc: ok


def callables() -> None:
    # str as a callable value fits a typed Callable slot
    xs = [3, 1, 20]
    print("map", list(map(str, xs)))
    print("sorted", sorted(xs, key=str))
    fs = [1.5, 2.0]
    print("mapf", list(map(str, fs)))
    print("apply", apply(str, 5))
    g: Callable[[int32], str] = str
    print("slot", g(9))


def user_class() -> None:
    # a user Stringable class goes through its own __str__
    p = Point(1, 2)
    print("user", gs(p), str(p))


def main() -> None:
    families()
    bool_elements()
    temporaries()
    literals()
    callables()
    user_class()
    part_subclass.main()


main()
