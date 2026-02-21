from tpy import Ptr, Int32
from tpy.unsafe import unsafe_load

def test() -> None:
    x: Int32 = Int32(5)
    p: Ptr[Int32] = Ptr(x)
    unsafe_load(p, Int32(0))  # tpyc: error(/No matching overload/)
