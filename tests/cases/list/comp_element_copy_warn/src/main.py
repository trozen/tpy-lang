# A comprehension storing a reference-type element into the owned result
# container deep-copies it (storage form), diverging from CPython aliasing --
# so it warns per the same model as the literal/append/assignment sinks. A
# fresh rvalue element, an explicit copy(), and a value-type element are exempt.
# Output is read-only (the warning is the acknowledgment of the silent copy).
from tpy import int32, copy


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def list_scalar(cells: list[Cell]) -> None:
    xs: list[Cell] = [c for c in cells]  # tpyc: warning(/copies Cell into owned storage/)
    print(len(xs))


def list_tuple_member(src: list[tuple[int32, Cell]]) -> None:
    xs: list[tuple[int32, Cell]] = [t for t in src]  # tpyc: warning(/copies Cell into owned storage/)
    print(len(xs))


def dict_value(cells: list[Cell]) -> None:
    d: dict[int32, Cell] = {c.v: c for c in cells}  # tpyc: warning(/copies Cell into owned storage/)
    print(len(d))


def exempt_fresh(n: int32) -> None:
    xs: list[Cell] = [Cell(i) for i in range(n)]  # tpyc: ok
    print(len(xs))


def exempt_copy(cells: list[Cell]) -> None:
    xs: list[Cell] = [copy(c) for c in cells]  # tpyc: ok
    print(len(xs))


def exempt_value(n: int32) -> None:
    xs: list[int32] = [i for i in range(n)]  # tpyc: ok
    print(len(xs))


def main() -> None:
    cells = [Cell(1), Cell(2)]
    src = [(1, Cell(5))]
    list_scalar(cells)
    list_tuple_member(src)
    dict_value(cells)
    exempt_fresh(2)
    exempt_copy(cells)
    exempt_value(3)


main()
