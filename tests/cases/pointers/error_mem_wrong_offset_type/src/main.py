from tpy import Ptr, Int32, take_ptr
from tpy.unsafe import unsafe_load

def test() -> None:
    x: Int32 = Int32(5)
    p: Ptr[Int32] = take_ptr(x)
    unsafe_load(p, Int32(0))  # tpyc: error(/No matching overload/)
