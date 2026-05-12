# Regression: hash() runs a Hashable protocol-conformance check on its argument.
# For Box[NotHashable] where Box has `__hash__[T: Hashable]`, the structural
# match shouldn't claim conformance just because Box has a __hash__ method --
# the class-shadowed bound has to be honored too.
from tpy import Hashable

class NotHashable:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


class Box[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def __hash__[T: Hashable](self) -> int:
        return hash(self.value)


def main() -> None:
    a = Box(NotHashable(1))
    h = hash(a)  # tpyc: error(/No matching overload for hash/)
    print(int(h))


main()
