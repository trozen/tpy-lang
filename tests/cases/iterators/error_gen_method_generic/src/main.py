# Error: generator methods on generic classes are not yet supported
from typing import Iterator
from tpy import Int32

class Box[T]:
    value: T
    def __init__(self, value: T) -> None:
        self.value = value

    def items(self) -> Iterator[T]:  # tpyc: error(/Generator methods on generic classes are not yet supported/)
        yield self.value
