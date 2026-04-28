# Phase 5 doesn't open a write path: `obj.X = ...` on a Final class constant
# routes through the same reassignment guard as `Class.X = ...`.
from typing import Final
from tpy import Int32


class C:
    LIMIT: Final[Int32] = 10

    def __init__(self) -> None:
        pass


def main() -> None:
    c = C()
    c.LIMIT = 20  # tpyc: error(/Cannot reassign Final class constant 'C.LIMIT'/)


main()
