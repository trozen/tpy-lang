from tpy import Ptr, ConstPtr, Int32, UInt32, Char, Array, copy
from tpy.unsafe import unsafe_ptr, unsafe_load, unsafe_store

def test_array_overload() -> None:
    arr: Array[Int32, 3] = [Int32(10), Int32(20), Int32(30)]
    p: Ptr[Int32] = unsafe_ptr(arr)
    print(unsafe_load(p, UInt32(1)))

def test_list_overload() -> None:
    lst: list[Int32] = [Int32(40), Int32(50), Int32(60)]
    p: Ptr[Int32] = unsafe_ptr(lst)
    print(unsafe_load(p, UInt32(2)))

def test_str_overload() -> None:
    s: str = "abc"
    cp: ConstPtr[Char] = unsafe_ptr(s)
    print(unsafe_load(cp, UInt32(0)))

def test_store_and_load() -> None:
    arr: Array[Int32, 2] = [Int32(0), Int32(0)]
    p: Ptr[Int32] = unsafe_ptr(arr)
    unsafe_store(p, UInt32(0), Int32(77))
    unsafe_store(p, UInt32(1), Int32(88))
    print(unsafe_load(p, UInt32(0)))
    print(unsafe_load(p, UInt32(1)))

test_array_overload()
test_list_overload()
test_str_overload()
test_store_and_load()
