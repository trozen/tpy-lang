from tpy import Ptr, int32, uint32, Array, readonly
from tpy.unsafe import unsafe_ptr, unsafe_load, unsafe_copy_n

def test_copy_mutable() -> None:
    src: Array[int32, 3] = [int32(10), int32(20), int32(30)]
    dst: Array[int32, 3] = [int32(0), int32(0), int32(0)]
    unsafe_copy_n(unsafe_ptr(dst), unsafe_ptr(src), uint32(3))
    print(unsafe_load(unsafe_ptr(dst), uint32(0)))
    print(unsafe_load(unsafe_ptr(dst), uint32(1)))
    print(unsafe_load(unsafe_ptr(dst), uint32(2)))

def test_copy_from_constptr() -> None:
    src: Array[int32, 3] = [int32(40), int32(50), int32(60)]
    dst: Array[int32, 3] = [int32(0), int32(0), int32(0)]
    sp: Ptr[int32] = unsafe_ptr(src)
    cp: Ptr[readonly[int32]] = sp
    unsafe_copy_n(unsafe_ptr(dst), cp, uint32(2))
    print(unsafe_load(unsafe_ptr(dst), uint32(0)))
    print(unsafe_load(unsafe_ptr(dst), uint32(1)))

test_copy_mutable()
test_copy_from_constptr()
