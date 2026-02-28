# @readonly lvalue alias of a non-value param gets const T* in C++.
from tpy import Int32, readonly


class Box:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value

    @readonly
    def get_value(self) -> Int32:
        return self.value


@readonly
def alias_param(b: Box) -> Int32:
    alias = b  # lvalue alias -> const Box*
    return alias.get_value()


def main() -> None:
    b = Box(Int32(42))
    print(alias_param(b))

main()
