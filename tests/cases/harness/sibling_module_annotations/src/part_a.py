# Sibling module A: sema-fact annotations (type, non_null/nullable, bounds,
# div, cast, Send/Sync of a declaration, frame traits of a generator).
from typing import Iterator
from tpy import Ptr, int8, int32, take_ptr


class Point:
    def __init__(self, x: int32) -> None:
        self.x = x


def show(p: Ptr[Point]) -> None:
    if p is not None:
        print("part_a show:", p.x)  # tpyc: non_null(p)


def maybe(p: Ptr[Point], flag: bool) -> None:
    if flag:
        print("part_a maybe:", p.x)  # tpyc: nullable(p)


# generator frame: holds only an int32, so it is Send and Sync
def count_up(n: int32) -> Iterator[int32]:  # tpyc: frame_send(yes) frame_sync(yes)
    i = 0
    while i < n:
        yield i
        i += 1


# an unconstrained index keeps its bounds check
def at(xs: list[int32], i: int32) -> int32:
    return xs[i]  # tpyc: bounds_checked(xs)


# an unconstrained int32 -> int8 narrowing keeps its range check
def narrow(v: int32) -> int8:
    return int8(v)  # tpyc: cast_checked(int8)


def main() -> None:
    s = "sibling"  # tpyc: type(StrView)
    print("part_a:", s)
    xs = [1, 2, 3]
    for i in range(len(xs)):
        print("part_a xs:", xs[i])  # tpyc: bounds_safe(xs)
    d = len(xs)
    if d != 0:
        print("part_a div:", 12 // d)  # tpyc: div_safe(d)
    pt = Point(4)
    show(take_ptr(pt))
    maybe(take_ptr(pt), True)
    # a fixed-width int declaration is Send and Sync
    k = 3  # tpyc: is_send(yes) is_sync(yes)
    print("part_a send:", k)
    for v in count_up(2):
        print("part_a frame:", v)
    print("part_a bounds:", at(xs, 1))
    print("part_a cast:", narrow(100))
