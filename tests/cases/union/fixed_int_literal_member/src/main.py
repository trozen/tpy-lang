# Which member a value or an int literal lands in at a `fixedint | int` union,
# independent of the canonical member order. No CPython run: the cpy stub's
# int32 is an int subclass, so isinstance(5, int32) is False there
# (BUGS.md#isinstance-int32-cpy-stub-subclass).
from tpy import int16, int32, int64, uint8, uint32


# free function: an int literal takes int32, the non-narrowing width
def fits() -> None:
    u: int32 | int = 5
    match u:
        case int32():
            print("fits int32")
        case int():
            print("fits int")


# free function: a literal outside the fixed member's range falls to int
def overflows() -> None:
    u: uint8 | int = -1
    match u:
        case uint8():
            print("overflows uint8")
        case int():
            print("overflows int")


# free function: a literal that fits a narrow unsigned width still falls to
# int, since int -> uint8 is a narrowing conversion for the C++ variant
def narrow_fits() -> None:
    u: uint8 | int = 5
    match u:
        case uint8():
            print("narrow-fits uint8")
        case int():
            print("narrow-fits int")


# free function: narrower but signed (int16) loses the same way
def narrow_signed() -> None:
    u: int16 | int = 5
    match u:
        case int16():
            print("narrow-signed int16")
        case int():
            print("narrow-signed int")


# free function: wide but unsigned (uint32) loses the same way
def wide_unsigned() -> None:
    u: uint32 | int = 5
    match u:
        case uint32():
            print("wide-unsigned uint32")
        case int():
            print("wide-unsigned int")


# free function: a literal at a wider signed width takes the width
def wide_fits() -> None:
    u: int64 | int = 5
    match u:
        case int64():
            print("wide-fits int64")
        case int():
            print("wide-fits int")


# parameter slot: a typed value lands in the member equal to its type
def exact_param(u: int32 | int) -> None:
    match u:
        case int32():
            print("exact-param int32")
        case int():
            print("exact-param int")


# parameter slot: a narrower typed value widens to the fixed member, not int
def widen_param(u: int64 | int) -> None:
    match u:
        case int64():
            print("widen-param int64")
        case int():
            print("widen-param int")


# return slot: same rule for the returned value
def exact_return(v: int32) -> int32 | int:
    return v


def check_return() -> None:
    r = exact_return(8)
    match r:
        case int32():
            print("exact-return int32")
        case int():
            print("exact-return int")


# field slot: a literal stored through a record field (a typed VALUE written
# to a union field is a lowering reject today, assign.field_write_shape)
class Holder:
    v: int32 | int

    def __init__(self) -> None:
        self.v = 9


def field_slot() -> None:
    h = Holder()
    match h.v:
        case int32():
            print("field int32")
        case int():
            print("field int")


def main() -> None:
    fits()
    overflows()
    narrow_fits()
    narrow_signed()
    wide_unsigned()
    wide_fits()
    x: int32 = 7
    exact_param(x)
    widen_param(x)
    check_return()
    field_slot()


main()
