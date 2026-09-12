# Multi-base same-name class constant: when both A and B declare LIMIT and the
# child doesn't redeclare, C3 would silently pick one. Mirror the
# instance-field ambiguity check and require explicit disambiguation.
from typing import Final
from tpy import int32


class A:
    LIMIT: Final[int32] = 1


class B:
    LIMIT: Final[int32] = 2


class C(A, B):
    pass


def main() -> None:
    print(C.LIMIT)  # tpyc: error(/Ambiguous class constant 'LIMIT' inherited from \{A, B\} in 'C'/)


main()
