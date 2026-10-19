# At --default-int=BigInt an enum lookup by a folded constant rejects exactly
# as the lookup by the hand literal `Code(8)` does, not with a C++ build error
# (BUGS.md#intenum-lookup-bigint-default-rejects).
from enum import IntEnum


class Code(IntEnum):
    A = 8


def main() -> None:
    print(Code(2 * 4) == Code.A)  # tpyc: error(/call\.enum_from_value\.arg/)


main()
