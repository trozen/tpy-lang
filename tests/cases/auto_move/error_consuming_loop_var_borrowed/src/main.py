# A `for` variable over a lent source refers to the caller's element, which the
# function does not own, so a consuming method cannot take it (CPython would run;
# BUGS.md#loop-var-consuming-receiver-borrowed-refused tracks the refusal).
from typing import Self
from tpy import Own, int32


class Box:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]

    def finish(self: Own[Self]) -> Own[list[int32]]:
        return self.xs


def f(ws: list[Box]) -> int32:
    rs: list[list[int32]] = []
    for b in ws:
        rs.append(b.finish())  # tpyc: error(/Cannot consume 'b' inside a loop/)
    return len(rs)


def main() -> None:
    ws = [Box(1), Box(2)]
    print(f(ws), len(ws[0].xs))


main()
