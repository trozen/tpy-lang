# unsafe_alloc/free/init/drop: raw heap memory management
from tpy import Ptr, Int32, UInt32
from tpy.unsafe import (
    unsafe_alloc, unsafe_alloc_n, unsafe_free, unsafe_init, unsafe_drop,
    unsafe_ptr_add, unsafe_load,
)

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def test_single() -> None:
    # Allocate, init, use via auto-deref, drop, free
    p: Ptr[Point] = unsafe_alloc()
    unsafe_init(p, Point(10, 20))
    print(p.x)
    print(p.y)
    unsafe_drop(p)
    unsafe_free(p)

def test_mutate() -> None:
    # Mutate fields through pointer after init
    p: Ptr[Point] = unsafe_alloc()
    unsafe_init(p, Point(1, 2))
    p.x = Int32(100)
    p.y = Int32(200)
    print(p.x, p.y)
    unsafe_drop(p)
    unsafe_free(p)

def test_alloc_n() -> None:
    # Allocate array, init each via ptr_add, read via unsafe_load
    p: Ptr[Point] = unsafe_alloc_n(UInt32(3))
    p1: Ptr[Point] = unsafe_ptr_add(p, 1)
    p2: Ptr[Point] = unsafe_ptr_add(p, 2)
    unsafe_init(p, Point(1, 2))
    unsafe_init(p1, Point(3, 4))
    unsafe_init(p2, Point(5, 6))
    print(unsafe_load(p, UInt32(0)).x, unsafe_load(p, UInt32(0)).y)
    print(unsafe_load(p, UInt32(1)).x, unsafe_load(p, UInt32(1)).y)
    print(unsafe_load(p, UInt32(2)).x, unsafe_load(p, UInt32(2)).y)
    unsafe_drop(p)
    unsafe_drop(p1)
    unsafe_drop(p2)
    unsafe_free(p)

def test_explicit_type_arg() -> None:
    # Explicit type argument when no inference context
    p: Ptr[Int32] = unsafe_alloc[Int32]()
    unsafe_init(p, Int32(99))
    print(unsafe_load(p, UInt32(0)))
    unsafe_drop(p)
    unsafe_free(p)

def test_alloc_n_value_type() -> None:
    # Array allocation with value types
    p: Ptr[Int32] = unsafe_alloc_n(UInt32(3))
    unsafe_init(p, Int32(10))
    unsafe_init(unsafe_ptr_add(p, 1), Int32(20))
    unsafe_init(unsafe_ptr_add(p, 2), Int32(30))
    print(unsafe_load(p, UInt32(0)))
    print(unsafe_load(p, UInt32(1)))
    print(unsafe_load(p, UInt32(2)))
    unsafe_drop(p)
    unsafe_drop(unsafe_ptr_add(p, 1))
    unsafe_drop(unsafe_ptr_add(p, 2))
    unsafe_free(p)

test_single()
test_mutate()
test_alloc_n()
test_explicit_type_arg()
test_alloc_n_value_type()
