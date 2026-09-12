# Error: Final[int32] arithmetic rejected when an operand is not a constant
from typing import Final
from tpy import int32

A: Final[int32] = 5
X: int32 = 10

BAD: Final[int32] = A + X  # tpyc: error(/compile-time constant/)
