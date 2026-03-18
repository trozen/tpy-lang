# Test Fn parameter on a class method (template header generation)
from tpy import Int32, Fn


class Processor:
    value: Int32

    def __init__(self, value: Int32) -> None:
        self.value = value

    def apply(self, f: Fn[[Int32], Int32]) -> Int32:
        return f(self.value)

    def apply_binary(self, other: Int32, f: Fn[[Int32, Int32], Int32]) -> Int32:
        return f(self.value, other)


def main() -> None:
    p = Processor(10)
    print(p.apply(lambda x: x * 2))
    print(p.apply(lambda x: x + 5))
    print(p.apply_binary(3, lambda x, y: x + y))


main()
