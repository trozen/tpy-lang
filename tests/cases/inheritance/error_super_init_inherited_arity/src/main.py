# `super().__init__()` through an `__init__`-less parent reaches the
# ancestor's `__init__`, so a missing argument is the ordinary arity error.
from tpy import int32


class Base:
    def __init__(self, x: int32) -> None:
        self.x = x


class Mid(Base):
    y: int32 = 0


class Leaf(Mid):
    def __init__(self) -> None:
        super().__init__()  # tpyc: error(/expects 1 argument\(s\), got 0/)


def main() -> None:
    leaf = Leaf()
    print(leaf.x)


main()
