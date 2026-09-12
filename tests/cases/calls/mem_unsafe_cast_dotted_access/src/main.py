import tpy.unsafe
from tpy import Ptr, int32, uint32, Array

arr: Array[int32, 2] = [int32(1), int32(2)]
p: Ptr[int32] = tpy.unsafe.unsafe_ptr(arr)
q: Ptr[uint32] = tpy.unsafe.unsafe_cast(p)
print(tpy.unsafe.unsafe_load(q, uint32(0)))
print(tpy.unsafe.unsafe_load(q, uint32(1)))
