from tpy import Ptr, uint8, uint32, char
from tpy.unsafe import unsafe_ptr, unsafe_copy_n

def test(s: str) -> None:
    buf: list[uint8] = [uint8(0)] * 10
    unsafe_copy_n(unsafe_ptr(buf), unsafe_ptr(s), uint32(3))  # tpyc: error(/Cannot infer type arguments/)
