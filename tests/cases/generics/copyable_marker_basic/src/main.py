# Direct exercise of the Copyable marker protocol as a shadow bound on a
# user-defined generic. Mirrors generic_method_per_method_bound but for the
# metadata-checked Copyable marker rather than the structural Comparable.
from tpy import Int32, Own, Copyable


class Cell[T]:
    value: T

    def __init__(self, value: Own[T]) -> None:
        self.value = value

    def duplicate[T: Copyable](self) -> T:
        return self.value


def main() -> None:
    c = Cell[Int32](42)
    print(c.duplicate())


main()
