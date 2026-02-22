# Error: Final variable requires constant initializer
from typing import Final
from tpy import Int32

def compute() -> Int32:
    return Int32(42)

X: Final[Int32] = compute()  # tpyc: error(/compile-time constant/)
