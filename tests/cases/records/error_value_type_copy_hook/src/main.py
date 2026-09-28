# A ValueType cannot define `__copy__`: it is copied implicitly wherever it
# is passed or bound, so the hook would run where the program never calls it.
from tpy import int32, ValueType


class Tok(ValueType):
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __copy__(self) -> "Tok":  # tpyc: error(/ValueType class 'Tok' cannot define '__copy__'/)
        return Tok(self.n)


def main() -> None:
    t = Tok(1)
    print(t.n)


main()
