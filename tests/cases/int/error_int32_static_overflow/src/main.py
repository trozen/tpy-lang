"""Test static overflow check for int32 literals.

The in-range boundary literals (2147483647, -2147483648 at an `int32`
annotation) are pinned by tests/cases/int/int32_boundary.
"""
from tpy import int32

too_big: int32 = 3000000000   # tpyc: error(/outside int32 range/)
# TODO: do not stop at first error and make this work
# too_small: int32 = -3000000000  # tpyc: error(/outside int32 range/)
