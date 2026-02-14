from tpy import ConstPtr, Char, UInt32
from tpy.mem import unsafe_ptr, unsafe_load

def test_str_ptr() -> None:
    s: str = "hello"
    cp: ConstPtr[Char] = unsafe_ptr(s)
    print(unsafe_load(cp, UInt32(0)))
    print(unsafe_load(cp, UInt32(4)))

test_str_ptr()
