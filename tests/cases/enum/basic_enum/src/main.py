# Basic enum: define, assign, print
from enum import Enum

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

def main() -> None:
    c: Color = Color.Red
    print(c)
    c = Color.Blue
    print(c)

main()
