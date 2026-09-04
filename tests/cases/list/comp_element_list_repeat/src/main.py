# A list-REPEAT as a comprehension element: the slot is its materialization
# target, so the element renders the self-describing from_range build rather
# than the lazy repeat_range. Value elements only: a reference element is
# copied per slot here as in every other repeat sink
# (BUGS.md#list-repeat-reference-elem-copy).
from tpy import Int32


def main() -> None:
    n = 3
    rows: list[list[float]] = [([0.0] * n) for _ in range(2)]  # tpyc: ok
    rows[0][0] = 1.0
    print(len(rows), len(rows[0]), rows[0][0], rows[1][0])
    # The dict-VALUE twin of the same element slot.
    table: dict[Int32, list[Int32]] = {i: ([0] * 2) for i in range(2)}  # tpyc: ok
    table[0][1] = 5
    print(len(table), len(table[0]), table[0][1], table[1][1])


main()
