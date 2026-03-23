# auto_readonly[Self] cannot be combined with @readonly.
from typing import Self
from tpy import Int32, readonly, auto_readonly

class Foo:
    x: Int32
    def __init__(self) -> None:
        self.x = Int32(1)

    @readonly
    def get(self: auto_readonly[Self]) -> Int32:  # tpyc: error(/auto_readonly\[Self\] cannot be combined with @readonly/)
        return self.x
