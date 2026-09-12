# Error: Final class constants cannot be reassigned.
from typing import Final
from tpy import int32


class C:
    LIMIT: Final[int32] = 10


def bump() -> None:
    C.LIMIT = int32(99)  # tpyc: error(/Cannot reassign Final class constant 'C.LIMIT'/)


bump()
