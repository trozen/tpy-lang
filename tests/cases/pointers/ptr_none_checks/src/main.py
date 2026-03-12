# Raw pointers should allow None assignment and None identity checks.
# This keeps Ptr()/nullptr semantics usable without Optional wrapping.
from tpy import Ptr, Int32, readonly

p: Ptr[Int32] = None  # tpyc: ok
print(p is None)

x: Int32 = Int32(7)
p = Ptr(x)
print(p is None)
print(p is not None)

p = None  # tpyc: ok
print(p is None)

cp: Ptr[readonly[Int32]] = None  # tpyc: ok
print(cp is None)
cp = Ptr(x)
print(cp is None)
print(cp is not None)
cp = None  # tpyc: ok
print(cp is None)

if p is not None:
    print(p.__deref__())
else:
    print(Int32(0))
