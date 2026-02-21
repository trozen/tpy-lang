from tpy import Ptr, Int32, UInt32
from tpy.unsafe import unsafe_ptr, unsafe_load

def test_list_ptr() -> None:
    items: list[Int32] = [Int32(10), Int32(20), Int32(30)]
    p: Ptr[Int32] = unsafe_ptr(items)
    print(unsafe_load(p, UInt32(0)))
    print(unsafe_load(p, UInt32(1)))
    print(unsafe_load(p, UInt32(2)))

test_list_ptr()
