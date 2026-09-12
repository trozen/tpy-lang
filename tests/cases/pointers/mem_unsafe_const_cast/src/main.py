from tpy import Ptr, int32, uint32, char, Array, readonly
from tpy.unsafe import unsafe_ptr, unsafe_load, unsafe_store, unsafe_const_cast

s: str = "hello"
cp: Ptr[readonly[char]] = unsafe_ptr(s)
p: Ptr[char] = unsafe_const_cast(cp)
print(unsafe_load(p, uint32(0)))
print(unsafe_load(p, uint32(4)))
