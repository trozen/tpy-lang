# Enum identity operators (is / is not) lowered to == / !=
from enum import Enum

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

def main() -> None:
    a: Color = Color.Red
    b: Color = Color.Red
    c: Color = Color.Blue
    print(a is b)
    print(a is c)
    print(a is not b)
    print(a is not c)

main()
