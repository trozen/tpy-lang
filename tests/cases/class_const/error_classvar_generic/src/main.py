# Error: ClassVar on generic classes lands with Final on generic classes (Phase 9).
from typing import ClassVar
from tpy import Int32


class Box[T]:
    counter: ClassVar[Int32] = 0  # tpyc: error(/class constants on generic classes are not yet supported/)
