# List concatenation with mismatched element types should error.
from tpy import int32

def main() -> None:
    a: list[int] = [1, 2]
    b: list[int32] = [3, 4]
    c = a + b  # tpyc: error(/operand/)

main()
