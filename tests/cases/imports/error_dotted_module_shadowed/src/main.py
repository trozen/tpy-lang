import tpy.unsafe
from tpy import Int32, Ptr, UInt32, Array

tpy: Int32 = Int32(1)
arr: Array[Int32, 2] = [Int32(1), Int32(2)]
p: Ptr[Int32] = tpy.unsafe.unsafe_ptr(arr)  # tpyc: error(/Cannot access field/)
