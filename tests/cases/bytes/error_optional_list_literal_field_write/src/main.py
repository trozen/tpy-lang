# The adjacent source shape that keeps rejecting at a list field: a local
# seeded by a list LITERAL resolves to `Array[int32, 2]`, not `list`, so its
# storage is not the field's container and no storage conversion spells one
# (BUGS.md#copy-array-literal-into-list-slot).
from tpy import int32


class Slot:
    xs: list[int32] | None

    def __init__(self) -> None:
        self.xs = None

    def put(self) -> None:
        v = [1, 2]
        self.xs = v  # tpyc: error(/field_write\.lift/)


def main() -> None:
    s = Slot()
    s.put()
    print(1)


main()
