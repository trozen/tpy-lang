# Enum equality and inequality comparison
from enum import Enum

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

def main() -> None:
    a: Color = Color.Red
    b: Color = Color.Red
    c: Color = Color.Blue
    print(a == b)
    print(a == c)
    print(a != b)
    print(a != c)

main()
