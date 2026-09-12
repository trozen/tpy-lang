# Error: cannot reassign a Final variable
from typing import Final
from tpy import int32

X: Final[int32] = 42
X = 99  # tpyc: error(/Cannot reassign Final/)
