# A ValueType whose `__init__` has a required keyword-only parameter after a
# defaulted first one cannot be built by type alone.
from tpy import int32, ValueType, make_default


class KW(ValueType):
    a: int32
    b: int32

    def __init__(self, a: int32 = 1, *, b: int32) -> None:
        self.a = a
        self.b = b


def main() -> None:
    # `b` has no default
    k = make_default[KW]()  # tpyc: error(/'KW' does not satisfy 'Default'/)
    print(k.a)


main()
