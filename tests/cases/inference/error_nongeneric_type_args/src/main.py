# Type args on non-generic function should be rejected.
from tpy import int32

def inc(x: int32) -> int32:
    return x + int32(1)

def main() -> None:
    y = inc[int32](int32(1))  # tpyc: error(/not generic/)

main()
