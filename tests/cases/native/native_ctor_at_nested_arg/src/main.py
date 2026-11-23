# A @native record constructor rvalue at a native callee's reference
# parameter: the argument converts at its parameter like any record, the
# construction lent for the call (`use_nr(::tpy::as_mut_lvalue(NR(x)))`).
# A fresh call or construction at a free call's parameter not declared
# const is lent so; a readonly one and a method stub's bind it in place.
from tpy.extern import native
from tpy import Own, int32, readonly
from nr_api import NR, bump_nr, push_list
import part_qualified


class Point:
    def __init__(self, v: int32) -> None:
        self.v = v

    def __copy__(self) -> Own[Point]:
        print("copy")
        return Point(self.v)

    def __add__(self, other: Point) -> Own[Point]:
        return Point(self.v + other.v)


@native('mylog::use_nr')
def use_nr(r: NR) -> int32: ...


@native('mylog::peek_nr')
def peek_nr(r: readonly[NR]) -> int32: ...


@native('mylog::point_v')
def point_v(p: Point) -> int32: ...


def make_nr(x: int32) -> Own[NR]:
    return NR(x)


def go(x: int32) -> int32:
    return use_nr(NR(x))  # tpyc: ok


def bumped(x: int32) -> int32:
    # a fresh argument at a native parameter whose C++ is a mutable `NR&`
    return bump_nr(NR(x))  # tpyc: ok


def made(x: int32) -> int32:
    # a fresh call result at a native `NR&` parameter
    return bump_nr(make_nr(x))  # tpyc: ok


def peeked(x: int32) -> int32:
    # a readonly parameter binds the construction in place
    return peek_nr(NR(x))  # tpyc: ok


def absorbed(x: int32) -> int32:
    # a native method stub binds the construction in place
    n = NR(x)
    return n.absorb(NR(x + 1))  # tpyc: ok


def pushed() -> int32:
    # a zero-argument construction at a native `std::vector&` parameter
    return push_list(list(), 7)  # tpyc: ok


def added() -> int32:
    # an operator result at a by-value native parameter: moved in, no copy
    a = Point(2)
    b = Point(3)
    return point_v(a + b)  # tpyc: ok


def main() -> None:
    print(go(2))
    print("bumped", bumped(4))
    print("made", made(4))
    print("peeked", peeked(3))
    print("absorbed", absorbed(3))
    print("pushed", pushed())
    print("added", added())
    part_qualified.main()


main()
