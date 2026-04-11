# Test that @export in native_module produces an error
# tpy: native_module
from tpy.extern import export
from tpy import Int32

@export(binding="C")
def bad_func(x: Int32) -> Int32:  # tpyc: error(/not allowed in native_module/)
    return x
