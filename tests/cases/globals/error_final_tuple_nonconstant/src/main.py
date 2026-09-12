# Error: Final tuple with non-constant element
from typing import Final
from tpy import int32

def get() -> int32:
    return int32(1)

BAD: Final[tuple[int32, int32]] = (get(), 2)  # tpyc: error(/compile-time constant/)
