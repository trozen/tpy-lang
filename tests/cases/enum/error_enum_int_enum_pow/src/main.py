# Error: power operator not supported on IntEnum
from enum import IntEnum

class P(IntEnum):
    A = 2

def main() -> None:
    x = P.A ** 3  # tpyc: error(/Invalid operand types/)

main()
