# An unmarshallable PARAMETER type is rejected the same way as a return type:
# the validation pass checks every @export param, not just the return.
# tpy: ext_module
from tpy.extern import export


@export
def f(x: str) -> int:  # tpyc: error(/parameter 'x'.*not yet marshallable/)
    return 0
