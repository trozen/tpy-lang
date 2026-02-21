# Test error: calling len() when bound doesn't include Sized
from __future__ import annotations
from typing import Protocol
from tpy import Int32

class Stringable(Protocol):
    def to_str(self) -> str: ...

def bad_call[T: Stringable](item: T) -> Int32:
    return len(item)  # tpyc: error(/No matching overload for len/)
