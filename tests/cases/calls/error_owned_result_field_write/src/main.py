# A field write into the new object a call returns is refused: the object
# is dropped after the statement (a documented restriction).
from tpy import int32, Own


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make() -> Own[Item]:
    return Item(1)


def main() -> None:
    make().n = 5  # tpyc: error(/the result of 'make\(\.\.\.\)' is a new object dropped after this statement/)
    print("done")


main()
