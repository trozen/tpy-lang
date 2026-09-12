# A record rvalue at a STRUCTURAL PROTOCOL slot inside a conditionally
# evaluated operand. The protocol temp is declared unspelled so the concept
# deduces the concrete type, and an unspelled temp cannot be deferred into
# the operand -- so it is refused rather than built ahead of the guard,
# where the constructor would run in a branch CPython never takes.
from typing import Protocol

from tpy import int32


class Reader(Protocol):
    def read(self) -> int32: ...


class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        print("built", n)
        self.n = n

    def read(self) -> int32:
        return self.n


def use(r: Reader) -> bool:
    return r.read() > 0


def either(b: bool) -> bool:
    return b or use(Src(7))  # tpyc: error(/not yet supported by C\+\+ code generation/)


def main() -> None:
    print(either(True))


main()
