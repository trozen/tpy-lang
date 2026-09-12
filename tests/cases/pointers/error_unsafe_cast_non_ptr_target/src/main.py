from tpy import Ptr, int32, uint32, Array
from tpy.unsafe import unsafe_ptr, unsafe_cast

arr: Array[int32, 2] = [int32(1), int32(2)]
p: Ptr[int32] = unsafe_ptr(arr)
x: int32 = unsafe_cast(p)  # tpyc: error(/target must be a pointer type/)
