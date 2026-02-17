import tpy.unsafe
from tpy import Ptr, Int32, UInt32, Array

arr: Array[Int32, 4] = [Int32(10), Int32(20), Int32(30), Int32(40)]
p: Ptr[Int32] = tpy.unsafe.unsafe_ptr(arr)
val: Int32 = tpy.unsafe.unsafe_load(p, UInt32(0))
print(val)
tpy.unsafe.unsafe_store(p, UInt32(1), Int32(99))
val2: Int32 = tpy.unsafe.unsafe_load(p, UInt32(1))
print(val2)
