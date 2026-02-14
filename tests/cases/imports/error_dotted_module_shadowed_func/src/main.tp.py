import tpy.mem
from tpy import Int32, Ptr, Array

def tpy() -> Int32:
    return Int32(1)

arr: Array[Int32, 2] = [Int32(1), Int32(2)]
p: Ptr[Int32] = tpy.mem.unsafe_ptr(arr)  # tpyc: error(/is not a variable/)
