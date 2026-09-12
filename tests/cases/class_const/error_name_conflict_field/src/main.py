# Error: a class constant cannot share a name with an instance field.
from typing import Final
from tpy import int32


class C:
    LIMIT: Final[int32] = 10  # tpyc: error(/'LIMIT' on 'C' is both a class constant and an instance field/)
    LIMIT: int32 = 0
