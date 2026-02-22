# Error: cannot use augmented assignment on Final variable
from typing import Final
from tpy import Int32

X: Final[Int32] = 42
X += 1  # tpyc: error(/Cannot reassign Final/)
