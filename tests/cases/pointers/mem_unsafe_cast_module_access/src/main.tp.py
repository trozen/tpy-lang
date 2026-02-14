import tpy.mem as m
from tpy import Ptr, Int32, UInt32, Array

arr: Array[Int32, 2] = [Int32(1), Int32(2)]
p: Ptr[Int32] = m.unsafe_ptr(arr)
q: Ptr[UInt32] = m.unsafe_cast(p)
print(m.unsafe_load(q, UInt32(0)))
print(m.unsafe_load(q, UInt32(1)))
