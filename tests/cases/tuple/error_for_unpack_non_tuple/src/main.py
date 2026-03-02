# Error: for-loop unpacking over non-tuple element type
from tpy import Int32

def main() -> None:
    items: list[Int32] = [1, 2]
    for a, b in items:  # tpyc: error(/Cannot unpack/)
        print(a, b)

main()
