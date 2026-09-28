# Two parent-initializer calls for the same base are rejected: CPython runs
# both (the second wins), C++ constructs a base once (LANGUAGE_FEATURES.md
# "Single class inheritance"). `super()` counts as the base it initializes.
from tpy import int32


class Base:
    n: int32

    def __init__(self, n: int32) -> None:
        print("base init", n)
        self.n = n


class Child(Base):
    def __init__(self) -> None:
        Base.__init__(self, 1)
        super().__init__(2)  # tpyc: error(/'Base' is initialized twice in 'Child\.__init__' \('Base\.__init__\(self, \.\.\.\)' already initializes it\)/)
        print("child", self.n)


def main() -> None:
    c = Child()
    print(c.n)


main()
