# Error: cannot use augmented assignment on Final variable
from typing import Final
from tpy import int32

X: Final[int32] = 42
X += 1  # tpyc: error(/Cannot reassign Final/)
