from tpy.extern import native
from tpy import int32

@native
@native(binding="C")
def bad_func(x: int32) -> int32: ...  # tpyc: error(/cannot have both @native and @native/)
