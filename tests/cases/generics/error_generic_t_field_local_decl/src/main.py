# Binding a generic record's `T` field to a LOCAL: the alias binding has no row
# for an open type param (neither an F1 record nor an alias-ref container), so
# the declaration rejects.
from tpy import int32, Own


class Cell[T]:
    value: T

    def __init__(self, v: Own[T]) -> None:
        self.value = v

    def copy_out(self) -> T:
        x = self.value  # tpyc: error(/decl.slot_type/)
        return x


def main() -> None:
    c = Cell[int32](1)
    print(c.copy_out())


main()
