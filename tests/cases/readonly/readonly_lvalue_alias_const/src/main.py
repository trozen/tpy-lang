# @readonly lvalue alias of a non-value param gets const T* in C++.
from tpy import int32, readonly


class Box:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value

    @readonly
    def get_value(self) -> int32:
        return self.value


@readonly
def alias_param(b: Box) -> int32:
    alias = b  # lvalue alias -> const Box*
    return alias.get_value()


def main() -> None:
    b = Box(int32(42))
    print(alias_param(b))

main()
