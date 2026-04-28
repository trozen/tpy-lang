# Error: instance-side reads of class constants land in Phase 5.
# v1 requires `<ClassName>.X`.
from typing import Final
from tpy import Int32


class C:
    LIMIT: Final[Int32] = 10

    def __init__(self) -> None:
        pass


def main() -> None:
    c = C()
    print(c.LIMIT)  # tpyc: error(/must be accessed as 'C.LIMIT', not through an instance/)


main()
