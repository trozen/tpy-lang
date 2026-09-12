from tpy import Ptr, int32, uint32, readonly, take_ptr
from tpy.unsafe import unsafe_load, unsafe_store

def test_store_and_load() -> None:
    x: int32 = int32(5)
    p: Ptr[int32] = take_ptr(x)
    unsafe_store(p, uint32(0), int32(99))
    val: int32 = unsafe_load(p, uint32(0))
    print(val)
    print(x)

def test_constptr_load() -> None:
    x: int32 = int32(42)
    cp: Ptr[readonly[int32]] = take_ptr(x)
    val: int32 = unsafe_load(cp, uint32(0))
    print(val)

test_store_and_load()
test_constptr_load()
