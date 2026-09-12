# Error: non-constant default value
from tpy import int32

x: int32 = int32(5)

def foo(a: int32 = x) -> int32:  # tpyc: error(/Default parameter value 'x' must be a module-level Final/)
    return a
