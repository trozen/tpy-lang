from tpy import Ptr, Int32, UInt32, Array, copy
from tpy.unsafe import unsafe_ptr, unsafe_load, unsafe_store

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def test_store_owned_object() -> None:
    arr: Array[Point, 2] = [Point(Int32(1), Int32(2)), Point(Int32(3), Int32(4))]
    p: Ptr[Point] = unsafe_ptr(arr)
    unsafe_store(p, UInt32(0), copy(Point(Int32(10), Int32(20))))
    loaded: Point = unsafe_load(p, UInt32(0))
    print(loaded.x)
    print(loaded.y)

test_store_owned_object()
