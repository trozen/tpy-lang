# tpy: native_module
from typing import Protocol
from tpy import Int32, readonly


class Sized(Protocol):
    @readonly
    def __len__(self) -> Int32: ...
