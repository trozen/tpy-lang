# A `= None` default whose parameter is a VALUE-form optional/union of a
# record has no usable C++ default argument: a free function is declared
# before any record is defined, so the class template would instantiate over
# an incomplete type. The default is materialized at the call site instead.
# Pointer-form members (a plain class) stay complete and keep the C++
# default, so both arms are pinned here.
from tpy import Int64, ValueType


def offset_of(tz: "Fixed | None" = None) -> Int64:
    if tz is None:
        return -1
    return tz.off


def kind_of(z: "Fixed | Wide | None" = None) -> Int64:
    if z is None:
        return -1
    if isinstance(z, Fixed):
        return z.off
    return z.span


def barks_of(d: "Dog | None" = None) -> Int64:
    if d is None:
        return -1
    return d.barks


class Fixed(ValueType):
    off: Int64

    def __init__(self, off: Int64) -> None:
        self.off = off


class Wide(ValueType):
    span: Int64

    def __init__(self, span: Int64) -> None:
        self.span = span


class Dog:
    barks: Int64

    def __init__(self, barks: Int64) -> None:
        self.barks = barks


def main() -> None:
    print(offset_of())
    print(offset_of(Fixed(7)))

    print(kind_of())
    print(kind_of(Fixed(3)))
    print(kind_of(Wide(88)))

    print(barks_of())
    print(barks_of(Dog(2)))


main()
