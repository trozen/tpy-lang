# match/case or-patterns and guards on enum subjects
from enum import Enum, auto

class Color(Enum):
    Red = auto()
    Green = auto()
    Blue = auto()

def classify(c: Color) -> str:
    match c:
        case Color.Red | Color.Blue:
            return "warm-ish"
        case Color.Green:
            return "green"
    return ""

def check(c: Color, allow_red: bool) -> str:
    match c:
        case Color.Red if allow_red:
            return "red allowed"
        case Color.Red:
            return "red blocked"
        case _:
            return "other"
    return ""

def main() -> None:
    print(classify(Color.Red))
    print(classify(Color.Blue))
    print(classify(Color.Green))
    print(check(Color.Red, True))
    print(check(Color.Red, False))
    print(check(Color.Green, True))

main()
