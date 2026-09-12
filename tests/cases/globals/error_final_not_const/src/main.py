# Error: Final variable requires constant initializer
from typing import Final
from tpy import int32

def compute() -> int32:
    return int32(42)

X: Final[int32] = compute()  # tpyc: error(/compile-time constant/)
