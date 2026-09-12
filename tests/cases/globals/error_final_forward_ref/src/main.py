# Error: Final cannot reference a later Final (forward reference)
from typing import Final
from tpy import int32

B: Final[int32] = A  # tpyc: error(/compile-time constant/)
A: Final[int32] = 10
