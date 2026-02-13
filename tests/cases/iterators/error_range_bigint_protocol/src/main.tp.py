from typing import OptIterator
from tpy import Int32

def f(it: OptIterator[Int32]) -> Int32:
    return 0

base = 1 << 100
print(f(range(base, base + 3)))  # tpyc: error(/does not conform to protocol/)
