from tpy import Ptr, Int32, UInt32, Char, Array, readonly
from tpy.unsafe import unsafe_ptr, unsafe_load, unsafe_store, unsafe_const_cast

s: str = "hello"
cp: Ptr[readonly[Char]] = unsafe_ptr(s)
p: Ptr[Char] = unsafe_const_cast(cp)
print(unsafe_load(p, UInt32(0)))
print(unsafe_load(p, UInt32(4)))
