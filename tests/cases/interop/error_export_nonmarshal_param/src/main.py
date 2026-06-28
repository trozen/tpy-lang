# An unmarshallable PARAMETER type is rejected the same way as a return type:
# the validation pass checks every @export param, not just the return. StrView
# is a borrow form, not marshallable across the boundary (use str/bytes).
# tpy: ext_module
from tpy import StrView
from tpy.extern import export


@export
def f(x: StrView) -> int:  # tpyc: error(/parameter 'x'.*not yet marshallable/)
    return 0
