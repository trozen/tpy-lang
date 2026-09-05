# The adjacent source shape that keeps rejecting at the same row: a local
# seeded by a list LITERAL resolves to `Array[Int32, 2]`, not `list`, so the
# name does not spell the field's container and the write has no render. The
# row keys on the SOURCE, not on which reference family the field is.
from tpy import Int32


class Slot:
    xs: list[Int32] | None

    def __init__(self) -> None:
        self.xs = None

    def put(self) -> None:
        v = [1, 2]
        self.xs = v  # tpyc: error(/assign.field_write_shape/)


def main() -> None:
    s = Slot()
    s.put()
    print(1)


main()
