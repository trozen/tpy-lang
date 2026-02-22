# Error: Final with unsupported type (list)
from typing import Final
from tpy import Int32

X: Final[list[Int32]] = []  # tpyc: error(/not supported/)
