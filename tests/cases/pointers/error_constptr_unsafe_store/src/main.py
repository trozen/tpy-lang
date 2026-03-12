from tpy import Ptr, Int32, UInt32, readonly
from tpy.unsafe import unsafe_store

def test() -> None:
    x: Int32 = Int32(5)
    cp: Ptr[readonly[Int32]] = Ptr(x)
    unsafe_store(cp, UInt32(0), Int32(99))  # tpyc: error(/requires a mutable pointer/)
