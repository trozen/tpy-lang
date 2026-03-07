# match/case on enum subject with value patterns (Color.RED)
from enum import Enum

class Color(Enum):
    Red = 0
    Green = 1
    Blue = 2

def describe(c: Color) -> str:
    match c:
        case Color.Red:
            return "red"
        case Color.Green:
            return "green"
        case _:
            return "other"

def main() -> None:
    print(describe(Color.Red))
    print(describe(Color.Green))
    print(describe(Color.Blue))

main()
