# match/case polymorphic dispatch where the arms name STRUCTURAL
# conformers of the @dynamic protocol (they implement its methods without
# inheriting it). Codegen routes these through tpy::dyn_adapter_cast, which
# unwraps the Adapter/RefAdapter the conformer sits inside -- a plain
# dynamic_cast would always fail.
from typing import Protocol
from tpy import dynamic


@dynamic
class Shape(Protocol):
    def area(self) -> int: ...


class Square:
    side: int

    def __init__(self, side: int) -> None:
        self.side = side

    def area(self) -> int:
        return self.side * self.side


class Rect:
    w: int
    h: int

    def __init__(self, w: int, h: int) -> None:
        self.w = w
        self.h = h

    def area(self) -> int:
        return self.w * self.h


def classify(s: Shape) -> str:
    match s:  # tpyc: ok
        case Square(side=k):
            return "square " + str(k) + " -> " + str(s.area())
        case Rect():
            return "rect -> " + str(s.area())
        case _:
            return "?"


def main() -> None:
    print(classify(Square(3)))
    print(classify(Rect(2, 5)))


main()
