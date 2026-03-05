# Using a typing protocol as type annotation without importing it should error.
from tpy import Int32

def first(items: Sequence[Int32]) -> Int32:  # tpyc: error(/from typing import Sequence/)
    return items[0]
