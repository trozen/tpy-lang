from tpy import Ptr, Int32, UInt32, readonly
from tpy.unsafe import unsafe_cast

cp: Ptr[readonly[Int32]] = Ptr[readonly[Int32]]()
p: Ptr[UInt32] = unsafe_cast(cp)  # tpyc: error(/cannot cast read-only pointer to mutable pointer/)
