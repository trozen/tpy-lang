# BigInt range does not conform to Iterator[int32]
from typing import Iterator
from tpy import int32

def f(it: Iterator[int32]) -> int32:
    return 0

base = 1 << 100
print(f(range(base, base + 3)))  # tpyc: error(/does not conform to protocol/)
