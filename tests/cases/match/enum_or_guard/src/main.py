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

def mixed(c: Color, allow: bool) -> str:
    match c:
        case Color.Red | Color.Blue:
            return "warm"
        case Color.Green if allow:
            return "guarded-green"
        case _:
            return "other"
    return ""

def or_guard(c: Color, flag: bool) -> str:
    match c:
        case Color.Red | Color.Blue if flag:
            return "warm+flag"
        case _:
            return "other"
    return ""

def multi_guard(c: Color, x: bool, y: bool) -> str:
    match c:
        case Color.Green if x:
            return "green+x"
        case Color.Green if y:
            return "green+y"
        case Color.Green:
            return "green"
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
    print(mixed(Color.Red, True))
    print(mixed(Color.Green, True))
    print(mixed(Color.Green, False))
    print(or_guard(Color.Red, True))
    print(or_guard(Color.Red, False))
    print(or_guard(Color.Green, True))
    print(multi_guard(Color.Green, True, True))
    print(multi_guard(Color.Green, False, True))
    print(multi_guard(Color.Green, False, False))
    print(multi_guard(Color.Red, True, True))

main()
