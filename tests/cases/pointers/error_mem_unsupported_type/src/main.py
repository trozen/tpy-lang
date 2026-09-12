from tpy import int32
from tpy.unsafe import unsafe_ptr

def test() -> None:
    x: int32 = int32(5)
    unsafe_ptr(x)  # tpyc: error(/No matching.*overload/)
