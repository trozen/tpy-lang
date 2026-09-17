# A SLICE in a for-each receiver is not a chain hop: `rows[i:i + 2]` mints a
# fresh SPAN over `rows` that lives only to the end of the for-head, so the
# element read off it names storage the loop's capture would outlive.
# BUGS.md#chain-temporary-hop-rejected. What compiles instead: index the
# container directly (`for b in rows[i + j]`), or iterate the slice as the
# WHOLE iterable. Binding the slice to a local is NOT a workaround: the decl
# rejects at `decl.slot_type`, annotated `Span[...]` or not
# (BUGS.md#rebound-element-borrow-local-rejects).
from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def bump(rows: list[list[Box]], i: int32, j: int32) -> None:
    for b in rows[i:i + 2][j]:  # tpyc: error(/iter.subscript_shape/)
        b.n += 5


def main() -> None:
    rows: list[list[Box]] = [[Box(1)]]
    bump(rows, 0, 0)


main()
