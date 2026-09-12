import tpy.unsafe as m
from tpy import Ptr, int32, uint32, Array

arr: Array[int32, 2] = [int32(1), int32(2)]
p: Ptr[int32] = m.unsafe_ptr(arr)
q: Ptr[uint32] = m.unsafe_cast(p)
print(m.unsafe_load(q, uint32(0)))
print(m.unsafe_load(q, uint32(1)))
