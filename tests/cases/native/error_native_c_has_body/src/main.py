from tpy.extern import native_c
from tpy import Int32

@native_c
def bad_func(x: Int32) -> Int32:  # tpyc: error(/must have `\.\.\.` body/)
    return x + Int32(1)
