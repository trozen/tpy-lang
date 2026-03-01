# Error: too many arguments (more than maximum)
from tpy import Int32

def add(a: Int32, b: Int32 = Int32(0)) -> Int32:
    return a + b

add(Int32(1), Int32(2), Int32(3))  # tpyc: error(/expects 1 to 2 arguments, got 3/)
