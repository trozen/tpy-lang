# A keyword-only parameter (after *) is rejected at the @export boundary: the
# unpack format does not yet mark the keyword-only boundary ($), so it would be
# accepted positionally too. Reject loudly until keyword-only support lands.
# tpy: ext_module
from tpy import Int64
from tpy.extern import export


@export
def f(a: Int64, *, b: Int64) -> Int64:  # tpyc: error(/keyword-only parameters/)
    return a + b
