# Error: cannot reassign a Final variable
from typing import Final
from tpy import Int32

X: Final[Int32] = 42
X = 99  # tpyc: error(/Cannot reassign Final/)
