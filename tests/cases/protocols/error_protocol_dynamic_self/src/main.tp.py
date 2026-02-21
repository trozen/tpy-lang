# Protocol with Self type cannot be @dynamic
from tpy import dynamic
from typing import Protocol, Self

@dynamic
class Addable(Protocol):  # tpyc: error(/@dynamic protocol 'Addable' cannot use Self type/)
    def add(self, other: Self) -> Self:
        ...
