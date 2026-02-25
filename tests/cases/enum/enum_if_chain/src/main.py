# Enum if/elif chain with comparison
from enum import Enum

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

def color_name(c: Color) -> str:
    if c == Color.Red:
        return "red"
    elif c == Color.Green:
        return "green"
    elif c == Color.Blue:
        return "blue"
    return "unknown"

def main() -> None:
    print(color_name(Color.Red))
    print(color_name(Color.Green))
    print(color_name(Color.Blue))

main()
