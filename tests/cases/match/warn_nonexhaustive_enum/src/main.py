# warning: non-exhaustive match on enum (missing member)
from enum import Enum, auto

class Color(Enum):
    Red = auto()
    Green = auto()
    Blue = auto()

def describe(c: Color) -> str:
    match c:  # tpyc: warning(/non-exhaustive match.*missing: Color.Blue.*case _:/)
        case Color.Red:
            return "red"
        case Color.Green:
            return "green"
    return "unknown"

def main() -> None:
    print(describe(Color.Red))
    print(describe(Color.Green))

main()
