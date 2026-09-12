# A pointer-repr `Optional[list]` FIELD write takes a target-threaded render the
# shared field-write tail does not spell, so it keeps rejecting.
from tpy import int32


class Bag:
    items: list[int32] | None

    def __init__(self, items: list[int32] | None) -> None:
        self.items = items

    def replace(self, items: list[int32] | None) -> None:
        self.items = items  # tpyc: error(/assign.field_write_shape/)


def main() -> None:
    b = Bag(None)
    b.replace([1, 2])
    print(b.items is None)


main()
