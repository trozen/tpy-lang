# Type name shadowed by local def -- Int32 not resolved in annotations
from tpy import Int32

def Int32() -> int:
    return 0

def foo(x: Int32) -> Int32:  # tpyc: error(/Int32/)
    return x
