# A mixed-width operator whose operands have no common type (neither widens
# into the other: int32 with uint32) stays a compile error.
from tpy import int32, uint32


def total(a: int32, b: uint32) -> None:
    print(a + b)  # tpyc: error(/Invalid operand types for '\+': int32 and uint32/)


total(1, 2)
