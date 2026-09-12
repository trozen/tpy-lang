from typing import Protocol
from tpy import int32

class Addable(Protocol):
    def add(self, other: int32) -> int32: ...

class WrongParams:
    value: int32

    # Wrong param type - takes str instead of int32
    def add(self, other: str) -> int32:
        return self.value

def process(item: Addable) -> None:
    pass

def main() -> None:
    x: WrongParams = WrongParams()
    process(x)  # tpyc: error(/does not conform to protocol/)

main()
