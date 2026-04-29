# Error: ClassVar[T] without an initializer is meaningless on a regular class
# (no Phase-1 instance-final reading either).
from typing import ClassVar
from tpy import Int32


class Counter:
    instances: ClassVar[Int32]  # tpyc: error(/ClassVar without an initializer is not supported/)
