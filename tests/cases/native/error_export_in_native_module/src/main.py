# Test that @export in native_module produces an error
# tpy: native_module
from tpy.extern import export
from tpy import int32

@export(binding="C")
def bad_func(x: int32) -> int32:  # tpyc: error(/not allowed in native_module/)
    return x
