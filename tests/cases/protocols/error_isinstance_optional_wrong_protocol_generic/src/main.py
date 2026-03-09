# isinstance on Optional[GenericProtocol[T]] must check the declared protocol
from __future__ import annotations
from typing import Protocol
from tpy import Int32

class HasLen(Protocol):
    def __len__(self) -> Int32: ...

class Container[T](Protocol):
    def __getitem__(self, index: Int32) -> T: ...

def test(x: Container[Int32] | None) -> None:
    if isinstance(x, HasLen):  # tpyc: error(/checks protocol 'HasLen', but variable is typed as 'Container\[Int32\] | None'/)
        print("wrong")
