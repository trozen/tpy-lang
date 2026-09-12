# Test that @export without binding="C" produces an error
from tpy.extern import export
from tpy import int32

@export  # tpyc: error(/requires binding/)
def bad_func(x: int32) -> int32:
    return x
