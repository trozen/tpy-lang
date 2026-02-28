# Test error: ValueType record cannot inherit from non-ValueType parent.
from tpy import Int32, ValueType


class Base:
    x: Int32


class Bad(Base, ValueType):  # tpyc: error(/parent 'Base' is not a value type/)
    y: Int32


def main() -> None:
    pass

main()
