# Error: Fn cannot be nested inside another type
from tpy import Fn, Int32

def apply(f: Fn[[Int32], Int32] | None, x: Int32) -> Int32:  # tpyc: error(/Fn type cannot be nested/)
    return x
