import tpy.unsafe
from tpy import Ptr, int32, uint32, Array

arr: Array[int32, 4] = [int32(10), int32(20), int32(30), int32(40)]
p: Ptr[int32] = tpy.unsafe.unsafe_ptr(arr)
val: int32 = tpy.unsafe.unsafe_load(p, uint32(0))
print(val)
tpy.unsafe.unsafe_store(p, uint32(1), int32(99))
val2: int32 = tpy.unsafe.unsafe_load(p, uint32(1))
print(val2)
