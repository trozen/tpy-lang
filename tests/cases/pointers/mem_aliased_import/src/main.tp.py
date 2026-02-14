import tpy.mem as m
from tpy import Ptr, Int32, UInt32, Array

arr: Array[Int32, 3] = [Int32(10), Int32(20), Int32(30)]
p: Ptr[Int32] = m.unsafe_ptr(arr)
print(m.unsafe_load(p, UInt32(0)))
m.unsafe_store(p, UInt32(2), Int32(99))
print(m.unsafe_load(p, UInt32(2)))
