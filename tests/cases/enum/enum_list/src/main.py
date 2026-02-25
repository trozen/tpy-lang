# Enum values stored in a list
from enum import Enum

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

def main() -> None:
    colors: list[Color] = [Color.Red, Color.Green, Color.Blue]
    for c in colors:
        print(c)
    print(len(colors))

main()
