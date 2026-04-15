from tpy import Int32
from typing import Protocol

class Printable(Protocol):
    def __str__(self) -> str:
        ...


class Measurable(Protocol):
    def size(self) -> Int32:
        ...


# Missing size() method from Measurable
class BadBox(Printable, Measurable):  # tpyc: error(/missing method.*size/)
    width: Int32

    def __init__(self, width: Int32) -> None:
        self.width = width

    def __str__(self) -> str:
        return "BadBox"
    # Missing: def size(self) -> Int32
