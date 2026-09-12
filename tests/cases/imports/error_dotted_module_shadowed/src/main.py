import tpy.unsafe
from tpy import int32, Ptr, uint32, Array

tpy: int32 = int32(1)
arr: Array[int32, 2] = [int32(1), int32(2)]
p: Ptr[int32] = tpy.unsafe.unsafe_ptr(arr)  # tpyc: error(/has no field/)
