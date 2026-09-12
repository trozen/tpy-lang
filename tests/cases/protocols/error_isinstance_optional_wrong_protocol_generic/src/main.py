# isinstance on Optional[GenericProtocol[T]] must check the declared protocol
from __future__ import annotations
from typing import Protocol
from tpy import int32

class HasLen(Protocol):
    def __len__(self) -> int32: ...

class Container[T](Protocol):
    def __getitem__(self, index: int32) -> T: ...

def test(x: Container[int32] | None) -> None:
    if isinstance(x, HasLen):  # tpyc: error(/checks protocol 'HasLen', but variable is typed as 'Container\[int32\] | None'/)
        print("wrong")
