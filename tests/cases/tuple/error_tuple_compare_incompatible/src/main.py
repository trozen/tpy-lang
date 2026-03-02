# Comparing tuples with incompatible element types
from tpy import Int32

def main() -> None:
    a: tuple[Int32, str] = (Int32(1), "x")
    b: tuple[Int32, Int32] = (Int32(1), Int32(2))
    print(a == b)  # tpyc: error(/Cannot compare tuple element/)

main()
