# Error: nested classes are not allowed inside generic classes
from tpy import Int32

class Container[T]:
    class Inner:  # tpyc: error(/Nested classes are not supported inside generic/)
        val: Int32

    value: T
