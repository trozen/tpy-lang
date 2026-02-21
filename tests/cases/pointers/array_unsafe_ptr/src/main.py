from tpy import Ptr, Int32, UInt32, Array
from tpy.unsafe import unsafe_ptr, unsafe_load, unsafe_store

def test_array_to_ptr() -> None:
    arr: Array[Int32, 4] = [Int32(10), Int32(20), Int32(30), Int32(40)]
    p: Ptr[Int32] = unsafe_ptr(arr)
    print(unsafe_load(p, UInt32(0)))
    print(unsafe_load(p, UInt32(1)))
    print(unsafe_load(p, UInt32(2)))
    print(unsafe_load(p, UInt32(3)))

def test_write_through_array_ptr() -> None:
    arr: Array[Int32, 3] = [Int32(1), Int32(2), Int32(3)]
    p: Ptr[Int32] = unsafe_ptr(arr)
    unsafe_store(p, UInt32(1), Int32(99))
    print(arr[Int32(1)])

test_array_to_ptr()
test_write_through_array_ptr()
