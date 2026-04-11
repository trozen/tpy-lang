# Test that @export(binding="X") produces an error
from tpy.extern import export
from tpy import Int32

@export(binding="X")  # tpyc: error(/only supports binding/)
def bad_func(x: Int32) -> Int32:
    return x
