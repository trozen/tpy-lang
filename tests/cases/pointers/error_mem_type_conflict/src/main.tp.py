from tpy import Ptr, ConstPtr, UInt8, UInt32, Char
from tpy.mem import unsafe_ptr, unsafe_copy_n

def test(s: str) -> None:
    buf: list[UInt8] = [UInt8(0)] * 10
    unsafe_copy_n(unsafe_ptr(buf), unsafe_ptr(s), UInt32(3))  # tpyc: error(/type parameter T inferred as UInt8 and Char/)
