# Test that @export(binding="X") produces an error
from tpy.extern import export
from tpy import int32

@export(binding="X")  # tpyc: error(/only supports binding/)
def bad_func(x: int32) -> int32:
    return x
