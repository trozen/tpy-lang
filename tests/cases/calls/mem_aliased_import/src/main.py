import tpy.unsafe as m
from tpy import Ptr, int32, uint32, Array

arr: Array[int32, 3] = [int32(10), int32(20), int32(30)]
p: Ptr[int32] = m.unsafe_ptr(arr)
print(m.unsafe_load(p, uint32(0)))
m.unsafe_store(p, uint32(2), int32(99))
print(m.unsafe_load(p, uint32(2)))
