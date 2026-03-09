# isinstance on Optional[Protocol] must check the declared protocol, not a different one
from __future__ import annotations
from typing import Protocol
from tpy import Int32

class HasLen(Protocol):
    def __len__(self) -> Int32: ...

class HasStr(Protocol):
    def __str__(self) -> str: ...

def test(x: HasLen | None) -> None:
    if isinstance(x, HasStr):  # tpyc: error(/checks protocol 'HasStr', but variable is typed as 'HasLen | None'/)
        print("wrong")
