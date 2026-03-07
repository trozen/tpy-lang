# error: too many positional patterns for the class
from dataclasses import dataclass

@dataclass
class Circle:
    radius: float

@dataclass
class Rect:
    width: float

def describe(s: Circle | Rect) -> str:
    match s:
        case Circle(r, extra):  # tpyc: error(/accepts 1 positional/)
            return "circle"
        case _:
            return "other"
    return ""

def main() -> None:
    pass

main()
