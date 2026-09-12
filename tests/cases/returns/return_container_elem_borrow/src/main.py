# A nested container's ELEMENT lvalue at a container BORROW return slot: the
# element read is already the reference the slot binds, so it returns bare.
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


main()
