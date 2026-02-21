# Builtin function with invalid explicit type argument.
from tpy import Ptr, Int32, Array
from tpy.unsafe import unsafe_ptr, unsafe_cast

def main() -> None:
    arr: Array[Int32, 2] = [Int32(1), Int32(2)]
    p: Ptr[Int32] = unsafe_ptr(arr)
    q = unsafe_cast[Nope](p)  # tpyc: error(/Unknown type/)

main()
