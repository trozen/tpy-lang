# Container subscript READS of enum elements -- list[Color] (literal and dynamic
# index) and dict[int32, Color] (fixed-int key), read as bare VALUE-form enum
# values. Exercises the compositional value-leaf subscript-read gate, which
# admits enum elements alongside scalars/str/bytes (the read renders the same
# `::tpy::__getitem__(c, i)` on both paths). Enums are value types, so a read is
# a copy on both paths -- no reference/aliasing distinction to force here.
from tpy import int32
from enum import Enum


class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2


def main() -> None:
    xs = [Color.Red, Color.Green, Color.Blue]
    print(xs[0])
    i = 2
    print(xs[i])
    d = {1: Color.Blue, 2: Color.Green}
    print(d[1])
    print(d[i])


main()
