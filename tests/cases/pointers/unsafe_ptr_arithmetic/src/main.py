# Pointer arithmetic: unsafe_ptr_add advances a pointer, unsafe_ptr_diff
# computes the element distance between two pointers.
from tpy import Ptr, int32, int64, Array
from tpy.unsafe import unsafe_ptr, unsafe_load, unsafe_ptr_add, unsafe_ptr_diff

arr: Array[int32, 4] = [int32(10), int32(20), int32(30), int32(40)]
base: Ptr[int32] = unsafe_ptr(arr)

# Advance pointer by 2 elements
p2: Ptr[int32] = unsafe_ptr_add(base, int64(2))
print(unsafe_load(p2, 0))

# Negative offset
p0: Ptr[int32] = unsafe_ptr_add(p2, int64(-2))
print(unsafe_load(p0, 0))

# Pointer difference
diff: int64 = unsafe_ptr_diff(p2, base)
print(diff)

# Reverse difference (negative)
diff2: int64 = unsafe_ptr_diff(base, p2)
print(diff2)
