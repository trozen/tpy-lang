# Tuple element assignment should be rejected (tuples are immutable)
from tpy import Int32

def main() -> None:
    t = (Int32(1), "hello")
    t[0] = Int32(42)  # tpyc: error(/immutable/)

main()
