# `r[0][0] += 1` through a user `__getitem__` would run it twice, so it is held
# at a reject (BUGS.md#augassign-call-receiver-double-eval).
from tpy import int32, readonly


class Rows:
    rows: list[list[int32]]
    calls: int32

    def __init__(self) -> None:
        self.rows = [[1], [2]]
        self.calls = 0

    @readonly(False)
    def __getitem__(self, i: int32) -> list[int32]:
        self.calls += 1
        return self.rows[i]


def main() -> None:
    r = Rows()
    r[0][0] += 1  # tpyc: error(/not yet supported by C\+\+ code generation/)
    print(r.rows[0], r.calls)


main()
