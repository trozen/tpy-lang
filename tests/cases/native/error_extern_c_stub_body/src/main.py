from tpy.extern import extern_c
from tpy import Int32

@extern_c
def bad_func(x: Int32) -> Int32: ...  # tpyc: error(/must have a body/)
