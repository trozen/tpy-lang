from tpy.extern import native
from tpy import Int32

@native(binding="C")
def bad_func(x: Int32) -> Int32:  # tpyc: error(/must have `\.\.\.` body/)
    return x + Int32(1)
