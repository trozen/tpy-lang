from tpy.extern import native
from tpy import int32

@native(binding="C")
def bad_func(x: int32) -> int32:  # tpyc: error(/must have `\.\.\.` body/)
    return x + int32(1)
