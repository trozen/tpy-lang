# Tuple element assignment should be rejected (tuples are immutable)
from tpy import int32

def main() -> None:
    t = (int32(1), "hello")
    t[0] = int32(42)  # tpyc: error(/immutable/)

main()
