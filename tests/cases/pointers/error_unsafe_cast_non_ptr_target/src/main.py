from tpy import Ptr, Int32, UInt32, Array
from tpy.unsafe import unsafe_ptr, unsafe_cast

arr: Array[Int32, 2] = [Int32(1), Int32(2)]
p: Ptr[Int32] = unsafe_ptr(arr)
x: Int32 = unsafe_cast(p)  # tpyc: error(/target must be a pointer type/)
