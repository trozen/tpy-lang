# An 8/16-bit operator over operands of mixed signedness stays refused, as at
# every width.
from tpy import int8, uint8


def total(a: int8, b: uint8) -> None:
    print(a + b)  # tpyc: error(/Invalid operand types for '\+': int8 and uint8/)


total(1, 2)
