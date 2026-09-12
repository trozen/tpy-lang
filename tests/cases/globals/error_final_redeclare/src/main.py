# Error: cannot declare the same Final variable twice
from typing import Final
from tpy import int32

X: Final[int32] = 10
X: Final[int32] = 20  # tpyc: error(/Cannot re-declare Final/)
