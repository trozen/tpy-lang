from typing import Protocol, Sequence
from tpy import Int32


class Child(Sequence[Int32], Protocol):  # tpyc: error(/Generic parent protocols are not yet supported/)
    pass
