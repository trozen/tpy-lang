# 8a.2: non-mutated record/container params get const T&; mutated ones stay T&.
# Also tests take_ptr(param) address-taking and Phase 2 call-graph propagation.
from tpy import int32, Ptr, take_ptr
from typing import Optional

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

# Non-mutated param: should become const Point& in codegen
def read_point(p: Point) -> int32:
    r: int32 = p.x  # tpyc: type(int32)
    return r

# Mutated param (field write): should stay Point&
def mutate_point(p: Point) -> None:
    p.x = int32(99)

# take_ptr(param) address-taking: must stay Point& (not const Point&)
def get_ptr(p: Point) -> Ptr[Point]:
    return take_ptr(p)

# items[i] subscript address-taking: items must stay vector& (not const)
def get_elem_ptr(items: list[Point], i: int32) -> Ptr[Point]:
    return items[i]

# Optional[Point] coercion: &(p) taken -- must stay Point&
def to_optional(p: Point) -> Optional[Point]:
    return p

# Non-mutated list param: should become const vector&
def sum_list(items: list[int32]) -> int32:
    total: int32 = 0
    for x in items:
        total += x
    return total

# Mutated list param (append): should stay vector&
def append_item(items: list[int32], v: int32) -> None:
    items.append(v)

# Phase 2 propagation: wrapper passes items to mutating callee -> items must stay T&
def append_wrapper(items: list[int32], v: int32) -> None:
    append_item(items, v)

def main() -> None:
    p = Point(int32(1), int32(2))
    print(read_point(p))
    mutate_point(p)
    print(p.x)

    pts: list[Point] = [Point(int32(10), int32(20)), Point(int32(30), int32(40))]
    ptr = get_elem_ptr(pts, int32(0))
    print(ptr.x)

    nums: list[int32] = [int32(10), int32(20)]
    print(sum_list(nums))
    append_item(nums, int32(30))
    print(sum_list(nums))
    append_wrapper(nums, int32(40))
    print(sum_list(nums))

main()
