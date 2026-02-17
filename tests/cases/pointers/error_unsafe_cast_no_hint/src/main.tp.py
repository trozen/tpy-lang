from tpy import Ptr, Int32, UInt32, Array
from tpy.unsafe import unsafe_ptr, unsafe_cast

arr: Array[Int32, 2] = [Int32(1), Int32(2)]
p: Ptr[Int32] = unsafe_ptr(arr)
print(unsafe_cast(p))  # tpyc: error(/requires a type argument or target type annotation/)
