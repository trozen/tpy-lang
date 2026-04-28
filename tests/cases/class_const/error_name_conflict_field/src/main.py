# Error: a class constant cannot share a name with an instance field.
from typing import Final
from tpy import Int32


class C:
    LIMIT: Final[Int32] = 10  # tpyc: error(/'LIMIT' on 'C' is both a class constant and an instance field/)
    LIMIT: Int32 = 0
