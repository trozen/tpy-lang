# unsafe_store with a record variable (no copy() needed)
from tpy import Ptr, int32, uint32, Array
from tpy.unsafe import unsafe_ptr, unsafe_load, unsafe_store

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def main() -> None:
    arr: Array[Point, 2] = [Point(1, 2), Point(3, 4)]
    p: Ptr[Point] = unsafe_ptr(arr)
    pt: Point = Point(10, 20)
    unsafe_store(p, 0, pt)
    loaded: Point = unsafe_load(p, 0)
    print(loaded.x)
    print(loaded.y)

main()
