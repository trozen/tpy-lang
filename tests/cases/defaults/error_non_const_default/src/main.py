# Error: non-constant default value
from tpy import Int32

x: Int32 = Int32(5)

def foo(a: Int32 = x) -> Int32:  # tpyc: error(/Default parameter value must be a constant/)
    return a
