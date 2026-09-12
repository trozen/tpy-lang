# An explicitly spelled dunder call with a RECORD argument. Sema stamps the
# user dunder with its C++ operator template, so the call renders as the
# operator over both operands -- the argument interpolates as a bare name,
# which is what a `const Acc&` binding spells.
#
# `Acc` is @nocopy on purpose: the argument crosses a boundary, and a bare
# name is only the right render if the slot BORROWS. Were it to copy, this
# would stop compiling rather than pass while quietly diverging from
# CPython, which always aliases here.
from tpy import int32, nocopy


@nocopy
class Acc:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __add__(self, other: 'Acc') -> int32:
        return self.v + other.v

    def __eq__(self, other: 'Acc') -> bool:
        return self.v == other.v


def total(a: Acc, b: Acc) -> int32:
    return a.__add__(b)  # the record arg interpolates into the + template


def same(a: Acc, b: Acc) -> bool:
    return a.__eq__(b)


def main() -> None:
    x = Acc(1)
    y = Acc(2)
    print(total(x, y))
    print(same(x, y), same(x, Acc(1)))


main()
