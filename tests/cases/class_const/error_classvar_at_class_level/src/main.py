# Error: ClassVar at class body level is reserved for Phase 7;
# v1 only supports Final[T] = value as a class constant.
from typing import ClassVar
from tpy import Int32


class Counter:
    instances: ClassVar[Int32] = 0  # tpyc: error(/ClassVar at class level is not yet supported/)
