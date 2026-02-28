# Test error: non-value type used as type argument for ValueType bound.
from tpy import Int32, ValueType


class NotValue:
    x: Int32


class Box[T: ValueType]:
    val: T


def main() -> None:
    b = Box[NotValue](NotValue(1))  # tpyc: error(/does not satisfy bound 'ValueType'/)

main()
