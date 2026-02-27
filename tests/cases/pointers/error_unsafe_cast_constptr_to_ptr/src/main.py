from tpy import Ptr, ReadOnlyPtr, Int32, UInt32
from tpy.unsafe import unsafe_cast

cp: ReadOnlyPtr[Int32] = ReadOnlyPtr[Int32]()
p: Ptr[UInt32] = unsafe_cast(cp)  # tpyc: error(/cannot cast read-only pointer to mutable pointer/)
