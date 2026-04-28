# Error: class constants on generic classes land in Phase 9.
from typing import Final
from tpy import Int32


class Box[T]:
    DEFAULT_SIZE: Final[Int32] = 10  # tpyc: error(/class constants on generic classes are not yet supported/)
