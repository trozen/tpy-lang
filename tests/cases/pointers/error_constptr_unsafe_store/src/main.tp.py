from tpy import ConstPtr, Int32, UInt32
from tpy.unsafe import unsafe_store

def test() -> None:
    x: Int32 = Int32(5)
    cp: ConstPtr[Int32] = ConstPtr(x)
    unsafe_store(cp, UInt32(0), Int32(99))  # tpyc: error(/requires a mutable Ptr/)
