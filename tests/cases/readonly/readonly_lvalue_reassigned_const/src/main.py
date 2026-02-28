# @readonly lvalue-reassigned alias uses const T* pointer-local in C++.
from tpy import Int32, readonly


class Box:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value

    @readonly
    def get_value(self) -> Int32:
        return self.value


@readonly
def pick(a: Box, b: Box, flag: bool) -> Int32:
    x = a
    if flag:
        x = b
    return x.get_value()


def main() -> None:
    a = Box(Int32(10))
    b = Box(Int32(20))
    print(pick(a, b, True))
    print(pick(a, b, False))

main()
