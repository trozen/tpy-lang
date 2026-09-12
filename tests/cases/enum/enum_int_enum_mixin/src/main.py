# IntEnum with explicit int8 underlying type via mixin syntax
from enum import Enum
from tpy import int8

class SmallEnum(int8, Enum):
    A = 0
    B = 1
    C = 127

def main() -> None:
    print(SmallEnum.A)
    print(SmallEnum.C)

    # Arithmetic gives int8
    x: int8 = SmallEnum.B + int8(10)
    print(x)

    # Ordering
    print(SmallEnum.A < SmallEnum.C)

    # Int comparison
    print(SmallEnum.B == int8(1))

main()
