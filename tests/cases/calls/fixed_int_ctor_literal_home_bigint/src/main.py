# Under `--default-int=BigInt` a fixed-int constructor's literal still binds
# its generic conversion at the type it builds, while a free generic call's
# literal binds the default int (`int`): the literal sections
# of `calls/fixed_int_ctor_literal_home`.
import tpy
from tpy import (Fn, dispatch, int8, int16, int32, int64, uint8, uint16, uint32,
                 uint64)

G = uint32(0xbb67ae85)  # tpyc: ok


class Rec:
    n: int8
    w: uint64

    def __init__(self) -> None:
        self.n = int8(-128)  # tpyc: ok
        self.w = uint64(0xffffffffffffffff)  # tpyc: ok


def takes(x: int16, y: uint32) -> str:
    return f"{x} {y}"


def ret_lit() -> int64:
    return int64(-9223372036854775808)  # tpyc: ok


def defaults(a: int8 = int8(-5), b: int32 = 1, c: uint32 = uint32(0xbb67ae85)) -> str:  # tpyc: ok
    return f"{a} {b} {c}"


def contextual[T, U](f: Fn[[T], U], x: T) -> U:
    return f(x)


@dispatch
def twin[T, U](f: Fn[[T], U], x: T) -> U:
    return f(x)
@dispatch
def twin[T, U](f: Fn[[T, T], U], x: T) -> U:
    return f(x, x)


def plain[T](x: T) -> T:
    return x


class C:
    def __init__(self) -> None:
        pass

    def m[T](self, x: T) -> T:
        return x


def main() -> None:
    # local binding: in range, negative, min values, beyond int32, beyond int64
    a = int32(5)  # tpyc: ok
    b = int8(-128)  # tpyc: ok
    c = uint32(0xbb67ae85)  # tpyc: ok
    d = int64(9007199254740993)  # tpyc: ok
    e = uint64(18446744073709551615)  # tpyc: ok
    f = int16(-32768)  # tpyc: ok
    g = int8(1 + 2)  # tpyc: ok
    print("local", a, b, c, d, e, f, g)
    # field and global initializer
    r = Rec()
    print("field", r.n, r.w, G)
    # argument
    print("arg", takes(int16(-3), uint32(4000000000)))  # tpyc: ok
    # return
    print("return", ret_lit())
    # container literal element
    xs = [uint8(255), uint8(0)]  # tpyc: ok
    print("list", xs)
    # comprehension element
    print("comp", [uint16(65535) for _ in range(2)])  # tpyc: ok
    # f-string operand and format spec
    print(f"fstr {uint32(0xbb67ae85)} {int8(-1)} {uint32(0xbb67ae85):x}")  # tpyc: ok
    # native call argument
    print("hex", hex(uint32(0xbb67ae85)))  # tpyc: ok
    # subscript store value and augmented subscript value
    # (a uint32 list: a narrower element is BUGS.md#narrow-list-literal-setitem-conversion)
    zs = [uint32(0), uint32(1)]
    zs[0] = uint32(0xbb67ae85)  # tpyc: ok
    zs[1] |= uint32(2)  # tpyc: ok
    print("subscript", zs)
    # parameter default, trailing and gap-filled
    print("default", defaults(), defaults(c=uint32(1)))  # tpyc: ok
    # module-qualified constructor, of a literal and of a converted value
    x: tpy.int32 = tpy.int32(10)  # tpyc: ok
    y: tpy.int32 = tpy.int32(tpy.uint32(5))  # tpyc: ok
    print("module_qualified", x, y)
    # a free generic call's literal binds the default int
    p = plain(5)  # tpyc: type(int)
    print("free", p)
    # an int literal beyond int32 binding a type parameter through a lambda's
    # hint, at a single generic and an overloaded twin, and nested in a tuple
    o1 = contextual(lambda x: x, 3144134277)  # tpyc: type(int)
    o2 = twin(lambda x: x, 3144134277)  # tpyc: type(int)
    o3 = plain((3144134277, 1))  # tpyc: type(/tuple\[int, int\]/)
    print("over", o1, o2, o3)
    # a generic method's literal beyond int32
    mm = C().m(3144134277)  # tpyc: type(int)
    print("method", mm)


main()
