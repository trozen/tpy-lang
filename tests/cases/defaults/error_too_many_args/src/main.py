# Error: too many arguments (more than maximum)
from tpy import int32

def add(a: int32, b: int32 = int32(0)) -> int32:
    return a + b

add(int32(1), int32(2), int32(3))  # tpyc: error(/expects 1 to 2 arguments, got 3/)
