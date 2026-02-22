# Error: Final cannot reference a later Final (forward reference)
from typing import Final
from tpy import Int32

B: Final[Int32] = A  # tpyc: error(/compile-time constant/)
A: Final[Int32] = 10
