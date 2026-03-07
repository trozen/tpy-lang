# match/case mixing positional and keyword patterns
from dataclasses import dataclass

@dataclass
class Point:
    x: float
    y: float
    z: float

@dataclass
class Label:
    text: str

def describe(s: Point | Label) -> None:
    match s:
        case Point(px, py, z=pz):
            print(px + py + pz)
        case Label(t):
            print(t)

def main() -> None:
    p: Point | Label = Point(1.0, 2.0, 3.0)
    describe(p)
    la: Point | Label = Label("hello")
    describe(la)

main()
