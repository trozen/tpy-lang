# Explicitly annotated Int32 variable errors on aug-assign with float operand
# (consistent with y = y * 1.5 already being a type error)
from tpy import Int32

def test() -> None:
    y: Int32 = 10
    y *= 1.5  # tpyc: error(/Type mismatch.*expected Int32/)

test()
