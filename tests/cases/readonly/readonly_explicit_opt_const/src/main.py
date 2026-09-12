# @readonly with explicit Optional[T] annotation and later readonly param assignment.
from tpy import int32, readonly


class Box:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value

    @readonly
    def get_value(self) -> int32:
        return self.value


@readonly
def maybe_read(b: Box, flag: bool) -> int32:
    x: Box | None = None
    if flag:
        x = b
    if x is not None:
        return x.get_value()
    return int32(0)


def main() -> None:
    b = Box(int32(42))
    print(maybe_read(b, True))
    print(maybe_read(b, False))

main()
