"""Test static overflow check for Int32 literals.

The in-range boundary literals (2147483647, -2147483648 at an `Int32`
annotation) are pinned by tests/cases/int/int32_boundary.
"""
from tpy import Int32

too_big: Int32 = 3000000000   # tpyc: error(/outside Int32 range/)
# TODO: do not stop at first error and make this work
# too_small: Int32 = -3000000000  # tpyc: error(/outside Int32 range/)
