# Test Fn parameter on a class method (template header generation)
from tpy import int32, Fn


class Processor:
    value: int32

    def __init__(self, value: int32) -> None:
        self.value = value

    def apply(self, f: Fn[[int32], int32]) -> int32:
        return f(self.value)

    def apply_binary(self, other: int32, f: Fn[[int32, int32], int32]) -> int32:
        return f(self.value, other)


def main() -> None:
    p = Processor(10)
    print(p.apply(lambda x: x * 2))
    print(p.apply(lambda x: x + 5))
    print(p.apply_binary(3, lambda x, y: x + y))


main()
