# Error: Final class constants cannot be reassigned.
from typing import Final
from tpy import Int32


class C:
    LIMIT: Final[Int32] = 10


def bump() -> None:
    C.LIMIT = Int32(99)  # tpyc: error(/Cannot reassign Final class constant 'C.LIMIT'/)


bump()
