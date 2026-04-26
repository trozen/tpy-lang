from typing import Protocol
from tpy import Int32


class Parent[T](Protocol):
    def get(self) -> T: ...


class Child(Parent, Protocol):  # tpyc: error(/Generic protocol .* requires type arguments/)
    pass
