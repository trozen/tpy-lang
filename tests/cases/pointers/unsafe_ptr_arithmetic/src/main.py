# Pointer arithmetic: unsafe_ptr_add advances a pointer, unsafe_ptr_diff
# computes the element distance between two pointers.
from tpy import Ptr, Int32, Int64, Array
from tpy.unsafe import unsafe_ptr, unsafe_load, unsafe_ptr_add, unsafe_ptr_diff

arr: Array[Int32, 4] = [Int32(10), Int32(20), Int32(30), Int32(40)]
base: Ptr[Int32] = unsafe_ptr(arr)

# Advance pointer by 2 elements
p2: Ptr[Int32] = unsafe_ptr_add(base, Int64(2))
print(unsafe_load(p2, 0))

# Negative offset
p0: Ptr[Int32] = unsafe_ptr_add(p2, Int64(-2))
print(unsafe_load(p0, 0))

# Pointer difference
diff: Int64 = unsafe_ptr_diff(p2, base)
print(diff)

# Reverse difference (negative)
diff2: Int64 = unsafe_ptr_diff(base, p2)
print(diff2)
