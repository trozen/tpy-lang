# Test that @export requires a body (not stub)
from tpy.extern import export
from tpy import int32

@export(binding="C")
def bad_func(x: int32) -> int32: ...  # tpyc: error(/must have a body/)
