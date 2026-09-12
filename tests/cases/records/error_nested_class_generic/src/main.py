# Error: nested classes are not allowed inside generic classes
from tpy import int32

class Container[T]:
    class Inner:  # tpyc: error(/Nested classes are not supported inside generic/)
        val: int32

    value: T
