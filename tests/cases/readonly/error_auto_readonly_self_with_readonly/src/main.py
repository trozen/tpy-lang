# auto_readonly[Self] cannot be combined with @readonly.
from typing import Self
from tpy import int32, readonly, auto_readonly

class Foo:
    x: int32
    def __init__(self) -> None:
        self.x = int32(1)

    @readonly
    def get(self: auto_readonly[Self]) -> int32:  # tpyc: error(/auto_readonly\[Self\] cannot be combined with @readonly/)
        return self.x
