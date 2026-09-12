# Error: a value type must declare an explicit __init__ (no synthesized
# aggregate ctor for an immutable type).
from tpy import int32, ValueType


class Vec2(ValueType):  # tpyc: error(/ValueType class 'Vec2' must define an explicit __init__/)
    x: int32
    y: int32


def main() -> None:
    pass


main()
