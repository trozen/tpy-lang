# Enum as function parameter and return type
from enum import Enum

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

def next_color(c: Color) -> Color:
    if c.value == 0:
        return Color.Green
    if c.value == 1:
        return Color.Blue
    return Color.Red

def print_color(c: Color) -> None:
    print(c)

def main() -> None:
    c: Color = Color.Red
    print_color(c)
    c = next_color(c)
    print_color(c)
    c = next_color(c)
    print_color(c)

main()
