# Error: Final variable must have an initializer
from typing import Final
from tpy import Int32

X: Final[Int32]  # tpyc: error(/must have an initializer/)
