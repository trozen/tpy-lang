# Test that @native(binding="X") produces an error
from tpy.extern import native
from tpy import int32

@native(binding="X")  # tpyc: error(/only supports binding/)
def bad_func(x: int32) -> int32: ...
