# Duplicate method definition in the same class should be an error.
from tpy import Int32

class Foo:
    def bar(self) -> Int32:
        return Int32(1)

    def bar(self) -> Int32:  # tpyc: error(/defined twice/)
        return Int32(2)
