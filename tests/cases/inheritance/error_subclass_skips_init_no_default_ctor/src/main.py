# Skipping a parent initializer stays an error (not the skip warning) when the
# parent has no zero-argument form for C++ to build it with.
from tpy import int32


class Base:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __del__(self) -> None:
        pass


class Child(Base):
    extra: int32

    # the subject: `Base` has `__del__` and a required `__init__` parameter
    def __init__(self, e: int32) -> None:  # tpyc: error(/'Child.__init__' does not initialize base 'Base', but 'Base' cannot be constructed without arguments .*Call 'super\(\).__init__\(...\)' \(or 'Base.__init__\(self, ...\)'\) as its first statement/)
        self.extra = e


def main() -> None:
    print(Child(7).extra)


main()
