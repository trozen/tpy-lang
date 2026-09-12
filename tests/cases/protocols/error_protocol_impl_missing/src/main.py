from tpy import int32
from typing import Protocol

class Printable(Protocol):
    def __str__(self) -> str:
        ...


class BadPerson(Printable):  # tpyc: error(/missing method/)
    name: str
    # Missing __str__ method!
