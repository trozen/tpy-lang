from tpy import Int32
from tpy.unsafe import unsafe_ptr

def test() -> None:
    x: Int32 = Int32(5)
    unsafe_ptr(x)  # tpyc: error(/No matching.*overload/)
