# The subscript spelling of a field write into a new object
# (`__getitem__ -> Own[Holder]`) is refused as the call spelling is.
from tpy import int32, Own


class Holder:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]


class Bag:
    def __init__(self) -> None:
        pass

    def __getitem__(self, i: int32) -> Own[Holder]:
        return Holder(i)


def main() -> None:
    b = Bag()
    b[1].xs = [5]  # tpyc: error(/a new object dropped after this statement; a write into it is lost/)
    print("done")


main()
