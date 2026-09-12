# Generic alias as a class field type. Tests substitution in storage-
# form position (field, not local/param), which has different codegen
# sensitivity than borrow-form positions.
from tpy import int32

type Pair[T] = tuple[T, T]


class Holder:
    pair: Pair[int32]
    label: str

    def __init__(self) -> None:
        self.pair = (int32(3), int32(7))
        self.label = "h"


def main() -> None:
    h = Holder()
    print(h.label)
    print(h.pair)


main()
