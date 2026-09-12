# Explicitly annotated int32 variable errors on aug-assign with float operand
# (consistent with y = y * 1.5 already being a type error)
from tpy import int32

def test() -> None:
    y: int32 = 10
    y *= 1.5  # tpyc: error(/Type mismatch.*expected int32/)

test()
