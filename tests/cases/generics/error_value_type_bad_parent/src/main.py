# Test error: ValueType record cannot inherit from non-ValueType parent.
from tpy import int32, ValueType


class Base:
    x: int32


class Bad(Base, ValueType):  # tpyc: error(/parent 'Base' is not a value type/)
    y: int32


def main() -> None:
    pass

main()
