from tpy import Ptr, ConstPtr, Int32, UInt32
from tpy.mem import unsafe_cast

cp: ConstPtr[Int32] = ConstPtr[Int32]()
p: Ptr[UInt32] = unsafe_cast(cp)  # tpyc: error(/cannot cast ConstPtr to Ptr/)
