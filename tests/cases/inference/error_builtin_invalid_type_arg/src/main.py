# Builtin function with invalid explicit type argument.
from tpy import Ptr, int32, Array
from tpy.unsafe import unsafe_ptr, unsafe_cast

def main() -> None:
    arr: Array[int32, 2] = [int32(1), int32(2)]
    p: Ptr[int32] = unsafe_ptr(arr)
    q = unsafe_cast[Nope](p)  # tpyc: error(/Unknown type/)

main()
