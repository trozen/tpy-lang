# native_global(array=True) for C array globals (extern T name[])
# Generates incomplete array extern, which decays to pointer when used.
from tpy.extern import native_global
from tpy import Ptr, int16, int32, uint32
from tpy.unsafe import unsafe_load, unsafe_store

scores: Ptr[int16] = native_global("g_scores", binding="C", array=True)
ids: Ptr[int32] = native_global("g_ids", binding="C", array=True)

# Read array elements
print(unsafe_load(scores, uint32(0)))
print(unsafe_load(scores, uint32(2)))
print(unsafe_load(ids, uint32(1)))

# Write and read back
unsafe_store(scores, uint32(0), int16(99))
print(unsafe_load(scores, uint32(0)))
