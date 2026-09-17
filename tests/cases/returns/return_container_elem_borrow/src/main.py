# A nested container's ELEMENT lvalue at a container BORROW return slot: the
# element read is already the reference the slot binds, so it returns bare --
# for a one-hop receiver and for an element whose own receiver is an element
# (`cube[i][j]`), which is an ordinary rooted lvalue chain.
from tpy import int32, Own


class Flat:
    data: list[list[int32]]

    def __init__(self, data: Own[list[list[int32]]]) -> None:
        self.data = data

    def get_data(self, k: int32) -> list[int32]:
        return self.data[k % len(self.data)]  # tpyc: ok


def pick(rows: list[list[int32]], k: int32) -> list[int32]:
    # The same element lvalue off a plain container parameter.
    return rows[k]  # tpyc: ok


def pick_nested(cube: list[list[list[int32]]], i: int32,
                j: int32) -> list[int32]:
    # The element's own RECEIVER is an element: a rooted lvalue chain, so the
    # borrow is still into the root and returns bare.
    return cube[i][j]  # tpyc: ok


def main() -> None:
    f = Flat([[1, 2], [3, 4]])
    row = f.get_data(1)
    row[0] = 99
    # The borrow return aliases the field's element -- a copy would print 3.
    print(f.data[1][0])
    rows: list[list[int32]] = [[5, 6], [7, 8]]
    got = pick(rows, 0)
    got[1] = 60
    print(rows[0][1])
    cube: list[list[list[int32]]] = [[[1, 2]]]
    inner = pick_nested(cube, 0, 0)
    inner[0] = 42
    # Two element hops and still the root's storage -- a copy would print 1.
    print(cube[0][0][0])


main()
