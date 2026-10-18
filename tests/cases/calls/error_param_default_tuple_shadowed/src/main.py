# A Final tuple default filled at a call whose scope binds the tuple's
# name to something else is read through its defining module, which the
# tuple argument position does not lower yet: a compile error, never the
# caller's PAIR (BUGS.md#filled-default-constant-read-rejects).
from typing import Final
from tpy import int32
from defs import ft

PAIR: Final[tuple[int32, int32]] = (1, 1)


def main() -> None:
    print(ft(c=9))  # tpyc: error(/not yet supported by C\+\+ code generation/)
    print(PAIR[0])


main()
