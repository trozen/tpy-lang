# Error: too many arguments (more than maximum)
from tpy import int32

def add(a: int32, b: int32 = int32(0)) -> int32:
    return a + b

add(int32(1), int32(2), int32(3))  # tpyc: error(/takes 1 to 2 positional arguments, got 3/)
