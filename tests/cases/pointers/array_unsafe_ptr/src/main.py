from tpy import Ptr, int32, uint32, Array
from tpy.unsafe import unsafe_ptr, unsafe_load, unsafe_store

def test_array_to_ptr() -> None:
    arr: Array[int32, 4] = [int32(10), int32(20), int32(30), int32(40)]
    p: Ptr[int32] = unsafe_ptr(arr)
    print(unsafe_load(p, uint32(0)))
    print(unsafe_load(p, uint32(1)))
    print(unsafe_load(p, uint32(2)))
    print(unsafe_load(p, uint32(3)))

def test_write_through_array_ptr() -> None:
    arr: Array[int32, 3] = [int32(1), int32(2), int32(3)]
    p: Ptr[int32] = unsafe_ptr(arr)
    unsafe_store(p, uint32(1), int32(99))
    print(arr[int32(1)])

test_array_to_ptr()
test_write_through_array_ptr()
