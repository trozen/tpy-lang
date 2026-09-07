# A bool-RESULT `and` over a non-bool operand: the truthiness reasoning the
# C++ `&&` needs is outside the value-select the boolop arm renders, so
# `flag and n` still rejects. The bool-operand happy path is pinned by
# tests/cases/operators/logical_and_or_value.
from tpy import Int32


def both(flag: bool, n: Int32) -> bool:
    return flag and n  # tpyc: error(/binop\.shape/)


def main() -> None:
    print(both(True, 5))


main()
