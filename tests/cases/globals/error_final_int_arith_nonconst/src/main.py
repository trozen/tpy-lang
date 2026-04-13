# Error: Final[Int32] arithmetic rejected when an operand is not a constant
from typing import Final
from tpy import Int32

A: Final[Int32] = 5
X: Int32 = 10

BAD: Final[Int32] = A + X  # tpyc: error(/compile-time constant/)
