from tpy import Ptr, int32, uint32
from tpy.unsafe import unsafe_ptr, unsafe_load

def test_list_ptr() -> None:
    items: list[int32] = [int32(10), int32(20), int32(30)]
    p: Ptr[int32] = unsafe_ptr(items)
    print(unsafe_load(p, uint32(0)))
    print(unsafe_load(p, uint32(1)))
    print(unsafe_load(p, uint32(2)))

test_list_ptr()
