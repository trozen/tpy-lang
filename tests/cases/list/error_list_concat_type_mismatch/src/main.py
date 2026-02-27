# List concatenation with mismatched element types should error.
from tpy import Int32

def main() -> None:
    a: list[int] = [1, 2]
    b: list[Int32] = [3, 4]
    c = a + b  # tpyc: error(/operand/)

main()
