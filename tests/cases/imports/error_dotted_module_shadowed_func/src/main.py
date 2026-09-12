import tpy.unsafe
from tpy import int32, Ptr, Array

def tpy() -> int32:
    return int32(1)

arr: Array[int32, 2] = [int32(1), int32(2)]
p: Ptr[int32] = tpy.unsafe.unsafe_ptr(arr)  # tpyc: error(/is not a variable/)
