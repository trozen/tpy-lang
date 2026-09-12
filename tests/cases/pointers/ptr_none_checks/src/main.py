# Raw pointers should allow None assignment and None identity checks.
# This keeps Ptr()/nullptr semantics usable without Optional wrapping.
from tpy import Ptr, int32, readonly, take_ptr

p: Ptr[int32] = None  # tpyc: ok
print(p is None)

x: int32 = int32(7)
p = take_ptr(x)
print(p is None)
print(p is not None)

p = None  # tpyc: ok
print(p is None)

cp: Ptr[readonly[int32]] = None  # tpyc: ok
print(cp is None)
cp = take_ptr(x)
print(cp is None)
print(cp is not None)
cp = None  # tpyc: ok
print(cp is None)

if p is not None:
    print(p.__deref__())
else:
    print(int32(0))
