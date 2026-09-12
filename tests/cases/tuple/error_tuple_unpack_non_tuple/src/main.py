# Error: unpacking a non-tuple type
from tpy import int32

def main() -> None:
    x: int32 = 42
    a, b = x  # tpyc: error(/Cannot unpack non-tuple type/)

main()
