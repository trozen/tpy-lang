import tpy.unsafe
from tpy import Ptr, Int32, UInt32, Array

arr: Array[Int32, 2] = [Int32(1), Int32(2)]
p: Ptr[Int32] = tpy.unsafe.unsafe_ptr(arr)
q: Ptr[UInt32] = tpy.unsafe.unsafe_cast(p)
print(tpy.unsafe.unsafe_load(q, UInt32(0)))
print(tpy.unsafe.unsafe_load(q, UInt32(1)))
