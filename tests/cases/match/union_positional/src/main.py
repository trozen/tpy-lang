# match/case with positional class patterns via __match_args__
from dataclasses import dataclass

@dataclass
class Circle:
    radius: float

@dataclass
class Rect:
    width: float
    height: float

def describe(s: Circle | Rect) -> None:
    match s:
        case Circle(r):
            print(r)
        case Rect(_, h):
            print(h)

def main() -> None:
    c: Circle | Rect = Circle(5.0)
    describe(c)
    r: Circle | Rect = Rect(3.0, 4.0)
    describe(r)

main()
