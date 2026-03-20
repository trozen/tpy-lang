# 8a.2: non-mutated record/container params get const T&; mutated ones stay T&.
# Also tests take_ptr(param) address-taking and Phase 2 call-graph propagation.
from tpy import Int32, Ptr, take_ptr
from typing import Optional

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

# Non-mutated param: should become const Point& in codegen
def read_point(p: Point) -> Int32:
    r: Int32 = p.x  # tpyc: type(Int32)
    return r

# Mutated param (field write): should stay Point&
def mutate_point(p: Point) -> None:
    p.x = Int32(99)

# take_ptr(param) address-taking: must stay Point& (not const Point&)
def get_ptr(p: Point) -> Ptr[Point]:
    return take_ptr(p)

# take_ptr(items[i]) subscript address-taking: items must stay vector& (not const)
def get_elem_ptr(items: list[Point], i: Int32) -> Ptr[Point]:
    return take_ptr(items[i])

# Optional[Point] coercion: &(p) taken -- must stay Point&
def to_optional(p: Point) -> Optional[Point]:
    return p

# Non-mutated list param: should become const vector&
def sum_list(items: list[Int32]) -> Int32:
    total: Int32 = 0
    for x in items:
        total += x
    return total

# Mutated list param (append): should stay vector&
def append_item(items: list[Int32], v: Int32) -> None:
    items.append(v)

# Phase 2 propagation: wrapper passes items to mutating callee -> items must stay T&
def append_wrapper(items: list[Int32], v: Int32) -> None:
    append_item(items, v)

def main() -> None:
    p = Point(Int32(1), Int32(2))
    print(read_point(p))
    mutate_point(p)
    print(p.x)

    pts: list[Point] = [Point(Int32(10), Int32(20)), Point(Int32(30), Int32(40))]
    ptr = get_elem_ptr(pts, Int32(0))
    print(ptr.__deref__().x)

    nums: list[Int32] = [Int32(10), Int32(20)]
    print(sum_list(nums))
    append_item(nums, Int32(30))
    print(sum_list(nums))
    append_wrapper(nums, Int32(40))
    print(sum_list(nums))

main()
