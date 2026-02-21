from tpy import Ptr, ConstPtr, Int32, UInt32, Array
from tpy.unsafe import unsafe_ptr, unsafe_load, unsafe_copy_n

def test_copy_mutable() -> None:
    src: Array[Int32, 3] = [Int32(10), Int32(20), Int32(30)]
    dst: Array[Int32, 3] = [Int32(0), Int32(0), Int32(0)]
    unsafe_copy_n(unsafe_ptr(dst), unsafe_ptr(src), UInt32(3))
    print(unsafe_load(unsafe_ptr(dst), UInt32(0)))
    print(unsafe_load(unsafe_ptr(dst), UInt32(1)))
    print(unsafe_load(unsafe_ptr(dst), UInt32(2)))

def test_copy_from_constptr() -> None:
    src: Array[Int32, 3] = [Int32(40), Int32(50), Int32(60)]
    dst: Array[Int32, 3] = [Int32(0), Int32(0), Int32(0)]
    sp: Ptr[Int32] = unsafe_ptr(src)
    cp: ConstPtr[Int32] = sp
    unsafe_copy_n(unsafe_ptr(dst), cp, UInt32(2))
    print(unsafe_load(unsafe_ptr(dst), UInt32(0)))
    print(unsafe_load(unsafe_ptr(dst), UInt32(1)))

test_copy_mutable()
test_copy_from_constptr()
