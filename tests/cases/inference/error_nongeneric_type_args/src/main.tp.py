# Type args on non-generic function should be rejected.
from tpy import Int32

def inc(x: Int32) -> Int32:
    return x + Int32(1)

def main() -> None:
    y = inc[Int32](Int32(1))  # tpyc: error(/not generic/)

main()
