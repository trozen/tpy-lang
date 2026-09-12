# Test that generic user-defined protocols require type arguments
from typing import Protocol
from tpy import int32

class Container[T](Protocol):
    def get(self) -> T: ...

def use(c: Container) -> None:  # tpyc: error(/requires type arguments/)
    pass

def main() -> None:
    pass
