# An extended-slice assignment into a list of a native type that has no
# default constructor still compiles and replaces the selected elements.
# tpy: include("native_types.hpp")
from tpy import int32
from tpy.extern import native


@native("::NativeItem")
class Item:
    n: int32

    def __init__(self, n: int32): ...


def main() -> None:
    a: list[Item] = []
    a.append(Item(1))
    a.append(Item(2))
    a.append(Item(3))
    rhs: list[Item] = []
    rhs.append(Item(8))
    rhs.append(Item(9))
    a[::2] = rhs  # tpyc: ok
    for x in a:
        print(x.n)


main()
