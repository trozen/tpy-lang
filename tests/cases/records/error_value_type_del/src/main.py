# A ValueType cannot define `__del__`: a value is copied freely, so there is
# no single object whose release the finalizer would mark.
from tpy import int32, ValueType


class Tok(ValueType):
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __del__(self) -> None:  # tpyc: error(/ValueType class 'Tok' cannot define '__del__'/)
        print("del", self.n)


def main() -> None:
    t = Tok(1)
    print(t.n)


main()
