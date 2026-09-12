# A user-record `__contains__` whose needle is a COMPOSITE over a local that
# widens later in the body: same disposition, so the needle slot rejects.
from tpy import int32


def gi() -> int:
    return 1


class Bag:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2, 3, 4]

    def __contains__(self, k: int32) -> bool:
        return k == 1


def probe(b: Bag) -> None:
    p = 0
    print((p + 1) in b)  # tpyc: error(/binop\.shape/)
    p = gi()
    print(p)


def main() -> None:
    probe(Bag())


main()
