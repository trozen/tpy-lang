# A @dynamic protocol `P` and a class implementing it; pc.py declares
# another protocol of the same name.
from typing import Protocol
from tpy import dynamic


@dynamic
class P(Protocol):
    def value(self) -> int: ...

    def bump(self) -> None: ...


class C(P):
    def __init__(self, v: int) -> None:
        self.v = v

    def value(self) -> int:
        return self.v

    def bump(self) -> None:
        self.v += 1
