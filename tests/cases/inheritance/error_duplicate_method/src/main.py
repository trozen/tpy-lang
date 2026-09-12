# Duplicate method definition in the same class should be an error.
from tpy import int32

class Foo:
    def bar(self) -> int32:
        return int32(1)

    def bar(self) -> int32:  # tpyc: error(/defined twice/)
        return int32(2)
