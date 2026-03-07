# Error: += is not a valid augmented assignment operator for sets
from tpy import Int32

def main() -> None:
    s: set[Int32] = {1, 2}
    s += {3, 4}  # tpyc: error(/not supported for set/)

main()
