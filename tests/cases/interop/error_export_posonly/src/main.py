# A positional-only parameter (before /) is rejected at the @export boundary:
# the keyword-aware unpack names every param in its kwlist, so it cannot honor
# positional-only-ness. Reject loudly rather than expose it as keyword-able.
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
def f(a: Int64, /, b: Int64) -> Int64:  # tpyc: error(/positional-only parameters/)
    return a + b
