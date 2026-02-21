# Type args on module.func should not bypass module name shadowing.
from tpy import Int32, Ptr, UInt32, Array
import tpy.unsafe as m
from tpy.unsafe import unsafe_ptr

def main() -> None:
    arr: Array[Int32, 2] = [Int32(1), Int32(2)]
    p: Ptr[Int32] = unsafe_ptr(arr)
    m = Int32(42)
    q = m.unsafe_cast[UInt32](p)  # tpyc: error(/Cannot call method.*on type Int32/)

main()
