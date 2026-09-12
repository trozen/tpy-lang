# Error: nested enums are not allowed inside generic classes
from tpy import int32
from enum import Enum, auto

class Container[T]:
    class Kind(Enum):  # tpyc: error(/Nested enums are not supported inside generic/)
        A = auto()

    value: T
