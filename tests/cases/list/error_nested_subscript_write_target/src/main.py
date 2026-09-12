# A nested subscript as the WRITE target (`rows[0][1] = ...`) after narrowing:
# the setitem gate has no row for a subscript receiver. TPy rejects it today.
from tpy import int32


def write(rows: list[list[int32]] | None) -> None:
    if rows is None:
        return
    rows[0][1] = 9  # tpyc: error(/setitem.recv.subscript/)


def main() -> None:
    write([[1, 2]])


main()
