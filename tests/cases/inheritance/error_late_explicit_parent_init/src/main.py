# A `Base.__init__(self)` after other statements is rejected like a late
# `super().__init__()`: C++ would run it first, CPython runs it in place
# (LANGUAGE_FEATURES.md "Single class inheritance", first-statement rule).
from tpy import int32


class Base:
    n: int32

    def __init__(self) -> None:
        print("base init")
        self.n = 5


class Child(Base):
    def __init__(self) -> None:
        self.n = 1
        print("child before")
        Base.__init__(self)  # tpyc: error(/Base\.__init__\(self, \.\.\.\) must be the first statement in __init__/)
        print("child after", self.n)


def main() -> None:
    c = Child()
    print(c.n)


main()
