from tpy.extern import native
from tpy import Int32

@native
@native(binding="C")
def bad_func(x: Int32) -> Int32: ...  # tpyc: error(/cannot have both @native and @native/)
