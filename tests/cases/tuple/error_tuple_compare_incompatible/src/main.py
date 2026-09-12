# Comparing tuples with incompatible element types
from tpy import int32

def main() -> None:
    a: tuple[int32, str] = (int32(1), "x")
    b: tuple[int32, int32] = (int32(1), int32(2))
    print(a == b)  # tpyc: error(/Cannot compare tuple element/)

main()
