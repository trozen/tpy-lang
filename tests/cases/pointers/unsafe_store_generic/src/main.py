# unsafe_store called from a generic function forwarding Own[T] into the store
from tpy import Own, Ptr, Int32, UInt32, Array
from tpy.unsafe import unsafe_ptr, unsafe_load, unsafe_store

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

# val is Own[T] so the store consumes it -- a borrowed T would copy into the
# pointee's owned storage (and warn); the forwarder must pass ownership through.
def store_at[T](p: Ptr[T], idx: UInt32, val: Own[T]) -> None:
    unsafe_store(p, idx, val)

def main() -> None:
    nums: Array[Int32, 3] = [10, 20, 30]
    np: Ptr[Int32] = unsafe_ptr(nums)
    store_at(np, UInt32(1), Int32(99))
    print(unsafe_load(np, UInt32(1)))

    pts: Array[Point, 2] = [Point(1, 2), Point(3, 4)]
    pp: Ptr[Point] = unsafe_ptr(pts)
    store_at(pp, UInt32(0), Point(10, 20))
    print(unsafe_load(pp, UInt32(0)).x)
    print(unsafe_load(pp, UInt32(0)).y)

main()
