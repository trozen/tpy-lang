# Type name shadowed by local def -- int32 not resolved in annotations
from tpy import int32

def int32() -> int:
    return 0

def foo(x: int32) -> int32:  # tpyc: error(/int32/)
    return x
