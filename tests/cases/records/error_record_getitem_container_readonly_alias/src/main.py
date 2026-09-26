# A container returned by reference off a READONLY receiver is const: mutating
# it through a bound alias is rejected, as for a builtin nested element.
from tpy import int32, readonly


class Rows:
    rows: list[list[int32]]

    def __init__(self) -> None:
        self.rows = [[1], [2]]

    def __getitem__(self, i: int32) -> list[int32]:
        return self.rows[i]


def grow(r: readonly[Rows]) -> None:
    x = r[0]
    x.append(3)  # tpyc: error(/non-readonly method 'append' on readonly reference/)


def main() -> None:
    grow(Rows())


main()
