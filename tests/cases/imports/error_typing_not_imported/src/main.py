# Test error when using typing names without importing them
from tpy import Int32

def foo(x: Optional[Int32]) -> Int32:  # tpyc: error(/requires.*from typing import Optional/)
    if x is not None:
        return x
    return Int32(0)
