# A user-record `__add__` whose operand is a COMPOSITE over a local that widens
# later in the body: the operand slot runs its disposition for the whole body,
# so the composite has no admitted render.
from tpy import int32


def gi() -> int:
    return 1


class Bag:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2, 3, 4]

    def __add__(self, k: int32) -> int32:
        return k + 1


def probe(b: Bag) -> None:
    p = 0
    print(b + (p + 1))  # tpyc: error(/binop\.shape/)
    p = gi()
    print(p)


def main() -> None:
    probe(Bag())


main()
