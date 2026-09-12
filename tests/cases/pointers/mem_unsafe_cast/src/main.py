from tpy import Ptr, int32, uint32, Array
from tpy.unsafe import unsafe_ptr, unsafe_load, unsafe_cast

arr: Array[int32, 2] = [int32(1), int32(2)]
p: Ptr[int32] = unsafe_ptr(arr)
p2: Ptr[uint32] = unsafe_cast(p)
print(unsafe_load(p2, uint32(0)))
print(unsafe_load(p2, uint32(1)))
