# Enum .name and .value properties
from enum import Enum

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

def main() -> None:
    c: Color = Color.Green
    print(c.name)
    print(c.value)
    c = Color.Blue
    print(c.name)
    print(c.value)

main()
