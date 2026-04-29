# Aug-assign on a Final class constant is reassignment too -- reject like `=`.
from typing import Final
from tpy import Int32


class C:
    LIMIT: Final[Int32] = 10


def bump() -> None:
    C.LIMIT += 1  # tpyc: error(/Cannot reassign Final class constant 'C.LIMIT'/)


bump()
