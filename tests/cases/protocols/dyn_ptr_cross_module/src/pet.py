from typing import Protocol
from tpy import int32, dynamic


@dynamic
class Counter(Protocol):
    def bump(self, by: int32) -> None: ...
    def value(self) -> int32: ...
