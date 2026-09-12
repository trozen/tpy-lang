from tpy import Ptr, char, uint32, readonly
from tpy.unsafe import unsafe_ptr, unsafe_load

def test_str_ptr() -> None:
    s: str = "hello"
    cp: Ptr[readonly[char]] = unsafe_ptr(s)
    print(unsafe_load(cp, uint32(0)))
    print(unsafe_load(cp, uint32(4)))

test_str_ptr()
