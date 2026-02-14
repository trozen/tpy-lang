from tpy import Ptr, Int32, Array

def test_array_to_ptr() -> None:
    arr: Array[Int32, 4] = [Int32(10), Int32(20), Int32(30), Int32(40)]
    p: Ptr[Int32] = arr.unsafe_ptr()
    print(p.unsafe_load(Int32(0)))
    print(p.unsafe_load(Int32(1)))
    print(p.unsafe_load(Int32(2)))
    print(p.unsafe_load(Int32(3)))

def test_write_through_array_ptr() -> None:
    arr: Array[Int32, 3] = [Int32(1), Int32(2), Int32(3)]
    p: Ptr[Int32] = arr.unsafe_ptr()
    p.unsafe_store(Int32(1), Int32(99))
    print(arr[Int32(1)])

test_array_to_ptr()
test_write_through_array_ptr()
