from tpy import Ptr, Int32, UInt32, Array
from tpy.mem import unsafe_ptr, unsafe_load, unsafe_cast

arr: Array[Int32, 2] = [Int32(1), Int32(2)]
p: Ptr[Int32] = unsafe_ptr(arr)
p2: Ptr[UInt32] = unsafe_cast(p)
print(unsafe_load(p2, UInt32(0)))
print(unsafe_load(p2, UInt32(1)))
