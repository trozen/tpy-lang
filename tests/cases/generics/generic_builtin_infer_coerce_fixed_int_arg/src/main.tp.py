# Builtin generic overload: infer T from pointer arg while coercing delta.
# This verifies generic inference accepts fixed-int widening on concrete params.
from tpy import Array, Int32, Ptr
from tpy.unsafe import unsafe_ptr, unsafe_ptr_add, unsafe_load

arr: Array[Int32, 3] = [Int32(10), Int32(20), Int32(30)]
base: Ptr[Int32] = unsafe_ptr(arr)
delta: Int32 = Int32(1)

p1: Ptr[Int32] = unsafe_ptr_add(base, delta)  # tpyc: ok
print(unsafe_load(p1, 0))
