# A value-bound generic (`T: ValueType`) whose ctor AND an ordinary method take
# an `Own[T]` param and write it into a `T` field. For a value type this is a
# COPY (intended: value types copy), and the AST method body emits a bare
# `field = v` -- unlike the ctor member-init-list, which moves. This shape was a
# THIR-path compiler crash (a move-free field write built a no-op form convert);
# guards against that regression via the byte-diff.
from tpy import int32, Own, ValueType


class Cell[T: ValueType]:
    item: T

    def __init__(self, item: Own[T]) -> None:
        self.item = item  # tpyc: ok

    def replace(self, item: Own[T]) -> int32:
        self.item = item  # tpyc: ok
        return 1


def main() -> None:
    c: Cell[int32] = Cell[int32](11)
    print(c.item)
    c.replace(22)
    print(c.item)


main()
