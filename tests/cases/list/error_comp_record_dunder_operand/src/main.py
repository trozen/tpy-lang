# The adjacent operand position a comprehension still cannot fill: a USER
# record's `__add__`, whose arg slot is a plain method param rather than the
# container helper's inline template slot.
from tpy import Own


class Bag:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def __add__(self, other: list[int]) -> Own[list[int]]:
        out: list[int] = [self.n]
        for v in other:
            out.append(v)
        return out


def a(xs: list[int]) -> Own[list[int]]:
    b = Bag(1)
    return b + [el for el in xs]  # tpyc: error(/expr.list_comp/)


def main():
    xs: list[int] = [1, 2]
    print(len(a(xs)))


main()
