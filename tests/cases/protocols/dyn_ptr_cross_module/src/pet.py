from typing import Protocol
from tpy import Int32, dynamic


@dynamic
class Counter(Protocol):
    def bump(self, by: Int32) -> None: ...
    def value(self) -> Int32: ...
