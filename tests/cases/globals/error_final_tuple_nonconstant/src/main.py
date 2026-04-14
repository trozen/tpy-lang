# Error: Final tuple with non-constant element
from typing import Final
from tpy import Int32

def get() -> Int32:
    return Int32(1)

BAD: Final[tuple[Int32, Int32]] = (get(), 2)  # tpyc: error(/compile-time constant/)
