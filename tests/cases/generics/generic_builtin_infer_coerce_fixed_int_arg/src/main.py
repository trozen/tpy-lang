# Builtin generic overload: infer T from pointer arg while coercing delta.
# This verifies generic inference accepts fixed-int widening on concrete params.
from tpy import Array, int32, Ptr
from tpy.unsafe import unsafe_ptr, unsafe_ptr_add, unsafe_load

arr: Array[int32, 3] = [int32(10), int32(20), int32(30)]
base: Ptr[int32] = unsafe_ptr(arr)
delta: int32 = int32(1)

p1: Ptr[int32] = unsafe_ptr_add(base, delta)  # tpyc: ok
print(unsafe_load(p1, 0))
