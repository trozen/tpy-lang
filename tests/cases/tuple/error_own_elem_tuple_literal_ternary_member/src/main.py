# A TERNARY member inside a tuple literal at an Own-element tuple slot: it is
# neither an rvalue source nor a movable name, so the element has no arm and
# this is rejected today.
from tpy import Int32, Own


class A:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def read_owned(p: tuple[Own[A], Int32]) -> Int32:
    return p[1]


def use(f: bool) -> None:
    print(read_owned((A(1) if f else A(2), 3)))  # tpyc: error(/expr\.tuple_literal/)


def main() -> None:
    use(True)


main()
