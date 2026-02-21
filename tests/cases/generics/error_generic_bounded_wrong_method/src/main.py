# Test error: calling method not defined in protocol
from __future__ import annotations
from typing import Protocol

class Stringable(Protocol):
    def to_str(self) -> str: ...

def bad_method[T: Stringable](item: T) -> str:
    return item.other_method()  # tpyc: error(/Protocol 'Stringable' has no method 'other_method'/)
