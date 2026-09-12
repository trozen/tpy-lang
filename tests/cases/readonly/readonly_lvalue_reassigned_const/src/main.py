# @readonly lvalue-reassigned alias uses const T* pointer-local in C++.
from tpy import int32, readonly


class Box:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value

    @readonly
    def get_value(self) -> int32:
        return self.value


@readonly
def pick(a: Box, b: Box, flag: bool) -> int32:
    x = a
    if flag:
        x = b
    return x.get_value()


def main() -> None:
    a = Box(int32(10))
    b = Box(int32(20))
    print(pick(a, b, True))
    print(pick(a, b, False))

main()
