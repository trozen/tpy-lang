# Error: unpacking a non-tuple type
from tpy import Int32

def main() -> None:
    x: Int32 = 42
    a, b = x  # tpyc: error(/Cannot unpack non-tuple type/)

main()
