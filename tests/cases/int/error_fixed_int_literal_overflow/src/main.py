# Out-of-range literal in fixed-int constructor should be a compile error.
# The in-range boundary literals are pinned by tests/cases/int/uint8_basic
# (UInt8(0)) and tests/cases/int/fixed_int_wrap (Int8(127), Int8(-128)).
from tpy import UInt8

bad: UInt8 = UInt8(-3)         # tpyc: error(/UInt8 overflow.*outside range/)
