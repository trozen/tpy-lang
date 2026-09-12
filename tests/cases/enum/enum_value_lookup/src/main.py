# Test enum construction from integer value
from enum import Enum
from tpy import int32

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

def lookup(v: int32) -> Color:
    return Color(v)

def from_int(v: int) -> Color:
    return Color(v)

def main() -> None:
    print(Color(0))
    print(Color(1))
    print(Color(2))
    print(lookup(1))
    # BigInt (int) coerces to underlying type
    print(from_int(2))

main()
