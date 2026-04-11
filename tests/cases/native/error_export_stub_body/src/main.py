# Test that @export requires a body (not stub)
from tpy.extern import export
from tpy import Int32

@export(binding="C")
def bad_func(x: Int32) -> Int32: ...  # tpyc: error(/must have a body/)
