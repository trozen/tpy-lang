# Generic alias as a class field type. Tests substitution in storage-
# form position (field, not local/param), which has different codegen
# sensitivity than borrow-form positions.
from tpy import Int32

type Pair[T] = tuple[T, T]


class Holder:
    pair: Pair[Int32]
    label: str

    def __init__(self) -> None:
        self.pair = (Int32(3), Int32(7))
        self.label = "h"


def main() -> None:
    h = Holder()
    print(h.label)
    print(h.pair)


main()
