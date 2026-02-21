# native_c_global_array for C array globals (extern T name[])
# Generates incomplete array extern, which decays to pointer when used.
from tpy import native_c_global_array, Ptr, Int16, Int32, UInt32
from tpy.unsafe import unsafe_load, unsafe_store

scores: Ptr[Int16] = native_c_global_array("g_scores")
ids: Ptr[Int32] = native_c_global_array("g_ids")

# Read array elements
print(unsafe_load(scores, UInt32(0)))
print(unsafe_load(scores, UInt32(2)))
print(unsafe_load(ids, UInt32(1)))

# Write and read back
unsafe_store(scores, UInt32(0), Int16(99))
print(unsafe_load(scores, UInt32(0)))
