from tpy import Ptr, int32, take_ptr
from tpy.unsafe import unsafe_load

def test() -> None:
    x: int32 = int32(5)
    p: Ptr[int32] = take_ptr(x)
    unsafe_load(p, int32(0))  # tpyc: error(/No matching overload/)
