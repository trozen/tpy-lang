# @override on a protocol implementation method.
# Should compile cleanly with no warning (protocol conformance, not class hiding).
from tpy import int32
from typing import Protocol, override

class Measurable(Protocol):
    def measure(self) -> int32: ...


class Box(Measurable):
    volume: int32

    def __init__(self, v: int32) -> None:
        self.volume = v

    @override
    def measure(self) -> int32:  # tpyc: ok
        return self.volume


def main() -> None:
    b = Box(int32(42))
    print(b.measure())

main()
