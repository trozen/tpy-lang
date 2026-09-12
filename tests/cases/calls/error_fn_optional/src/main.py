# Error: Fn cannot be nested inside another type
from tpy import Fn, int32

def apply(f: Fn[[int32], int32] | None, x: int32) -> int32:  # tpyc: error(/Fn type cannot be nested/)
    return x
