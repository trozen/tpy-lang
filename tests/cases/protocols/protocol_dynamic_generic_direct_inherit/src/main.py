# Generic @dynamic protocol with direct inheritance: the C++ struct
# inherits Container<int32_t> directly, so no adapter wrap is emitted.
from typing import Protocol
from tpy import Int32, dynamic


@dynamic
class Container[T](Protocol):
    def get(self) -> T:
        ...


class IntBox(Container[Int32]):
    def get(self) -> Int32:
        return 42


def show(c: Container[Int32]) -> None:
    print(c.get())


def main() -> None:
    b = IntBox()
    show(b)
    c: Container[Int32] = IntBox()
    print(c.get())


main()
