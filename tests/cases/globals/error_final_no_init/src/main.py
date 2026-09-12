# Error: Final variable must have an initializer
from typing import Final
from tpy import int32

X: Final[int32]  # tpyc: error(/must have an initializer/)
