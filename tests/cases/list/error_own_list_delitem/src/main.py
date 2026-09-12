# `del` on an `Own[list[...]]` receiver: the move-in ABI's C++ shape is not the
# borrow the del emit assumes.
from tpy import int32, Own


class Row:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def drop_first(rows: Own[list[Row]]) -> int32:
    del rows[0]  # tpyc: error(/del_item:recv_or_index/)
    return len(rows)


def main() -> None:
    print(drop_first([Row(1), Row(2)]))


main()
