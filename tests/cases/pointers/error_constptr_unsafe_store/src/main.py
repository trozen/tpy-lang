from tpy import Ptr, int32, uint32, readonly, take_ptr
from tpy.unsafe import unsafe_store

def test() -> None:
    x: int32 = int32(5)
    cp: Ptr[readonly[int32]] = take_ptr(x)
    unsafe_store(cp, uint32(0), int32(99))  # tpyc: error(/Cannot infer type arguments/)
