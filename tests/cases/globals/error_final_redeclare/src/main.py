# Error: cannot declare the same Final variable twice
from typing import Final
from tpy import Int32

X: Final[Int32] = 10
X: Final[Int32] = 20  # tpyc: error(/Cannot re-declare Final/)
