import tpy.unsafe
from tpy import Int32 as tpy, Ptr, Array

arr: Array[tpy, 2] = [tpy(1), tpy(2)]
p: Ptr[tpy] = tpy.unsafe.unsafe_ptr(arr)  # tpyc: error(/is not a variable/)
