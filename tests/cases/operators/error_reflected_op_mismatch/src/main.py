# Guard: a reflected operator must not over-match. __radd__ takes an int64 left
# operand, so `1.5 + d` (float left) has no viable forward/reverse op and is rejected.
from tpy import ValueType, int64


class Dur(ValueType):
    nanos: int64

    def __init__(self, ns: int64) -> None:
        self.nanos = ns

    def __radd__(self, n: int64) -> int64:
        return n + self.nanos


def main() -> None:
    d = Dur(100)
    print(1.5 + d)  # tpyc: error(/Invalid operand types for '\+': float and Dur/)


main()
