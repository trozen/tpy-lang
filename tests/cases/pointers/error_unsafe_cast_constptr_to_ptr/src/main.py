from tpy import Ptr, int32, uint32, readonly
from tpy.unsafe import unsafe_cast

cp: Ptr[readonly[int32]] = Ptr[readonly[int32]]()
p: Ptr[uint32] = unsafe_cast(cp)  # tpyc: error(/cannot cast read-only pointer to mutable pointer/)
