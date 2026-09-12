# Error: Final with unsupported type (list)
from typing import Final
from tpy import int32

X: Final[list[int32]] = []  # tpyc: error(/not supported/)
