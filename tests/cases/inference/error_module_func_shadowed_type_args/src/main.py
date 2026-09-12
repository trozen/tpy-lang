# Type args on module.func should not bypass module name shadowing.
from tpy import int32, Ptr, uint32, Array
import tpy.unsafe as m
from tpy.unsafe import unsafe_ptr

def main() -> None:
    arr: Array[int32, 2] = [int32(1), int32(2)]
    p: Ptr[int32] = unsafe_ptr(arr)
    m = int32(42)
    q = m.unsafe_cast[uint32](p)  # tpyc: error(/Cannot call method.*on type int32/)

main()
