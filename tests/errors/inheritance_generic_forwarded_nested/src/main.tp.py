from tpy import Int32
from typing import Sequence

class Container[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value


class Child[T](Container[Sequence[T]]):  # tpyc: error(/Generic base class with forwarded type parameters not yet supported/)
    extra: Int32
