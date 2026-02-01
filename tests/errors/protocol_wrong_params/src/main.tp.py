from typing import Protocol
from tpy import Int32

class Addable(Protocol):
    def add(self, other: Int32) -> Int32: ...

class WrongParams:
    value: Int32

    # Wrong param type - takes str instead of Int32
    def add(self, other: str) -> Int32:
        return self.value

def process(item: Addable) -> None:
    pass

def main() -> None:
    x: WrongParams = WrongParams()
    process(x)  # tpyc: error(/does not conform to protocol/)

main()
