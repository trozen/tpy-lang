from tpy import Ptr, Char, UInt32, readonly
from tpy.unsafe import unsafe_ptr, unsafe_load

def test_str_ptr() -> None:
    s: str = "hello"
    cp: Ptr[readonly[Char]] = unsafe_ptr(s)
    print(unsafe_load(cp, UInt32(0)))
    print(unsafe_load(cp, UInt32(4)))

test_str_ptr()
