from tpy import Ptr, int32, uint32, char, Array, copy, readonly
from tpy.unsafe import unsafe_ptr, unsafe_load, unsafe_store

def test_array_overload() -> None:
    arr: Array[int32, 3] = [int32(10), int32(20), int32(30)]
    p: Ptr[int32] = unsafe_ptr(arr)
    print(unsafe_load(p, uint32(1)))

def test_list_overload() -> None:
    lst: list[int32] = [int32(40), int32(50), int32(60)]
    p: Ptr[int32] = unsafe_ptr(lst)
    print(unsafe_load(p, uint32(2)))

def test_str_overload() -> None:
    s: str = "abc"
    cp: Ptr[readonly[char]] = unsafe_ptr(s)
    print(unsafe_load(cp, uint32(0)))

def test_store_and_load() -> None:
    arr: Array[int32, 2] = [int32(0), int32(0)]
    p: Ptr[int32] = unsafe_ptr(arr)
    unsafe_store(p, uint32(0), int32(77))
    unsafe_store(p, uint32(1), int32(88))
    print(unsafe_load(p, uint32(0)))
    print(unsafe_load(p, uint32(1)))

test_array_overload()
test_list_overload()
test_str_overload()
test_store_and_load()
