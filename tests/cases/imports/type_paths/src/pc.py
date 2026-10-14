# A @dynamic protocol named like pa.P, with the same methods: a pa.C
# satisfies it structurally, not by inheritance.
from typing import Protocol
from tpy import dynamic


@dynamic
class P(Protocol):
    def value(self) -> int: ...

    def bump(self) -> None: ...
