from tpy import Ptr, ReadOnlyPtr, Int32, UInt32
from tpy.unsafe import unsafe_load, unsafe_store

def test_store_and_load() -> None:
    x: Int32 = Int32(5)
    p: Ptr[Int32] = Ptr(x)
    unsafe_store(p, UInt32(0), Int32(99))
    val: Int32 = unsafe_load(p, UInt32(0))
    print(val)
    print(x)

def test_constptr_load() -> None:
    x: Int32 = Int32(42)
    cp: ReadOnlyPtr[Int32] = ReadOnlyPtr(x)
    val: Int32 = unsafe_load(cp, UInt32(0))
    print(val)

test_store_and_load()
test_constptr_load()
