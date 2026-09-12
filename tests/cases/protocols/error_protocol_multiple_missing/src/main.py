from tpy import int32
from typing import Protocol

class Printable(Protocol):
    def __str__(self) -> str:
        ...


class Measurable(Protocol):
    def size(self) -> int32:
        ...


# Missing size() method from Measurable
class BadBox(Printable, Measurable):  # tpyc: error(/missing method.*size/)
    width: int32

    def __init__(self, width: int32) -> None:
        self.width = width

    def __str__(self) -> str:
        return "BadBox"
    # Missing: def size(self) -> int32
