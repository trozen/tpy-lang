# An enum-member default from a different enum than the field's declared
# type is rejected -- codegen would otherwise emit a type-mismatched C++
# initializer (`Color tint = Mode::A;`) that the C++ compiler rejects opaquely.
from enum import Enum


class Color(Enum):
    RED = 0
    GREEN = 1


class Mode(Enum):
    A = 0
    B = 1


class Shape:
    tint: Color = Mode.A  # tpyc: error(/does not match the declared field type 'Color'/)


def main() -> None:
    pass


main()
