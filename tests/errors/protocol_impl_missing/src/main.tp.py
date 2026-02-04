from tpy import Int32
from typing import Protocol

class Printable(Protocol):
    def __str__(self) -> str:
        ...


class BadPerson(Printable):  # tpyc: error(/missing required methods/)
    name: str
    # Missing __str__ method!
