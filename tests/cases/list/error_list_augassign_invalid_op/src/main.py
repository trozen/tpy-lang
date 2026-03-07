# Error: -= is not a valid augmented assignment operator for lists
from tpy import Int32

def main() -> None:
    a: list[Int32] = [1, 2, 3]
    a -= [1]  # tpyc: error(/not supported/)

main()
