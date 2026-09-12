# Out-of-range literal in fixed-int constructor should be a compile error.
# The in-range boundary literals are pinned by tests/cases/int/uint8_basic
# (uint8(0)) and tests/cases/int/fixed_int_wrap (int8(127), int8(-128)).
from tpy import uint8

bad: uint8 = uint8(-3)         # tpyc: error(/uint8 overflow.*outside range/)
