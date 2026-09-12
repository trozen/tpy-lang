# Test error when using typing names without importing them
from tpy import int32

def foo(x: Optional[int32]) -> int32:  # tpyc: error(/requires.*from typing import Optional/)
    if x is not None:
        return x
    return int32(0)
