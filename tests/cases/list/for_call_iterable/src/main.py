# For-each over container-returning calls: an Own[list]/Own[dict] return is an
# rvalue captured owning (auto __obj_N = ...), a borrow / readonly borrow return
# an lvalue (auto& __obj_N = ...). Field mutation through a borrow-returning
# call's loop var must be visible in the source list (aliasing, not a copy).
from tpy import int32, Own, readonly


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def make_list(n: int32) -> Own[list[int32]]:
    return [n, n + 1, n + 2]


def make_dict() -> Own[dict[int32, int32]]:
    return {1: 10, 2: 20}


def get_cells(cells: list[Cell]) -> list[Cell]:
    return cells


def view(items: list[int32]) -> readonly[list[int32]]:
    return items


def own_returns() -> None:
    total = int32(0)
    for x in make_list(4):
        total += x
    for k in make_dict():
        total += k
    print(total)


def bump(cells: list[Cell]) -> None:
    for c in get_cells(cells):
        c.v += 10


def readonly_sum(items: list[int32]) -> int32:
    s = int32(0)
    for y in view(items):
        s += y
    return s


def main() -> None:
    own_returns()
    cells = [Cell(1), Cell(2)]
    bump(cells)
    print(cells[0].v, cells[1].v)
    print(readonly_sum([7, 8]))


main()
