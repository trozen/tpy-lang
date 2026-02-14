import tpy.mem
from tpy import Ptr, Int32, UInt32, Array

arr: Array[Int32, 2] = [Int32(1), Int32(2)]
p: Ptr[Int32] = tpy.mem.unsafe_ptr(arr)
q: Ptr[UInt32] = tpy.mem.unsafe_cast(p)
print(tpy.mem.unsafe_load(q, UInt32(0)))
print(tpy.mem.unsafe_load(q, UInt32(1)))
