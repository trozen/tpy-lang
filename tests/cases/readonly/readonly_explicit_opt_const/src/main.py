# @readonly with explicit Optional[T] annotation and later readonly param assignment.
from tpy import Int32, readonly


class Box:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value

    @readonly
    def get_value(self) -> Int32:
        return self.value


@readonly
def maybe_read(b: Box, flag: bool) -> Int32:
    x: Box | None = None
    if flag:
        x = b
    if x is not None:
        return x.get_value()
    return Int32(0)


def main() -> None:
    b = Box(Int32(42))
    print(maybe_read(b, True))
    print(maybe_read(b, False))

main()
