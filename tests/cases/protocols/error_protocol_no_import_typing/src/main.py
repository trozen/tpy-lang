# Using a typing protocol as type annotation without importing it should error.
from tpy import int32

def first(items: Sequence[int32]) -> int32:  # tpyc: error(/from typing import Sequence/)
    return items[0]
