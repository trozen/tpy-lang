# *args is rejected at the @export boundary: the keyword-aware unpack marshals a
# fixed param list, so a vararg pack would be silently dropped. Reject loudly.
# tpy: ext_module
from tpy import int64
from tpy.extern import export


@export
def f(a: int64, *rest: int64) -> int64:  # tpyc: error(/\*args is not supported/)
    return a
