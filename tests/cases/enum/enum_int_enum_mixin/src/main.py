# IntEnum with explicit Int8 underlying type via mixin syntax
from enum import Enum
from tpy import Int8

class SmallEnum(Int8, Enum):
    A = 0
    B = 1
    C = 127

def main() -> None:
    print(SmallEnum.A)
    print(SmallEnum.C)

    # Arithmetic gives Int8
    x: Int8 = SmallEnum.B + Int8(10)
    print(x)

    # Ordering
    print(SmallEnum.A < SmallEnum.C)

    # Int comparison
    print(SmallEnum.B == Int8(1))

main()
