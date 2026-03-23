# auto_readonly[Self] on methods with method-level type params is not supported.
from typing import Self
from tpy import Int32, auto_readonly

class Foo:
    x: Int32
    def __init__(self) -> None:
        self.x = Int32(1)

    def get[T](self: auto_readonly[Self], val: T) -> T:  # tpyc: error(/auto_readonly on methods with method-level type parameters/)
        return val
