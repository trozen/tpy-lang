from tpy import Ptr, Int32, UInt32, readonly, take_ptr
from tpy.unsafe import unsafe_store

def test() -> None:
    x: Int32 = Int32(5)
    cp: Ptr[readonly[Int32]] = take_ptr(x)
    unsafe_store(cp, UInt32(0), Int32(99))  # tpyc: error(/Cannot infer type arguments/)
