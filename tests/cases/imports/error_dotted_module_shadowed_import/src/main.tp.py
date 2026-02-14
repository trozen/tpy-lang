import tpy.mem
from tpy import Int32 as tpy, Ptr, Array

arr: Array[tpy, 2] = [tpy(1), tpy(2)]
p: Ptr[tpy] = tpy.mem.unsafe_ptr(arr)  # tpyc: error(/is not a variable/)
