# An @inline method expands at the call site and emits no C++ definition of its
# own, so the expansion renders in place.
from tpy import int32, inline


def sink(n: int32) -> None:
    print(n)


class Box:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    @inline
    def emit(self) -> None:
        sink(self.v)


def main() -> None:
    b = Box(3)
    b.emit()  # expands to sink(b.v)


main()
