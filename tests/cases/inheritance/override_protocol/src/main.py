# @override on a protocol implementation method.
# Should compile cleanly with no warning (protocol conformance, not class hiding).
from tpy import Int32
from typing import Protocol, override

class Measurable(Protocol):
    def measure(self) -> Int32: ...


class Box(Measurable):
    volume: Int32

    def __init__(self, v: Int32) -> None:
        self.volume = v

    @override
    def measure(self) -> Int32:  # tpyc: ok
        return self.volume


def main() -> None:
    b = Box(Int32(42))
    print(b.measure())

main()
