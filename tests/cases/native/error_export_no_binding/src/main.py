# Test that @export without binding="C" produces an error
from tpy.extern import export
from tpy import Int32

@export  # tpyc: error(/requires binding/)
def bad_func(x: Int32) -> Int32:
    return x
