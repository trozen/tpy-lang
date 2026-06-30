# A default parameter value is rejected at the @export boundary: the keyword-
# aware unpack has no optional slot, so silently requiring the arg would diverge
# from the Python source (where f(1) is valid). Reject loudly instead.
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
def f(a: Int64, b: Int64 = 5) -> Int64:  # tpyc: error(/default parameter values are not supported/)
    return a + b
