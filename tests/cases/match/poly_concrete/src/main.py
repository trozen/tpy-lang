# match/case polymorphic dispatch on a CONCRETE polymorphic-class subject
# (a class tree rooted at a markerless @dynamic protocol). A class pattern
# naming the subject's own root type acts as a catch-all (its dynamic_cast
# always succeeds), so no separate wildcard is needed for exhaustiveness.
from typing import Protocol
from tpy import dynamic


@dynamic
class Tag(Protocol):
    pass


class Animal(Tag):
    legs: int

    def __init__(self, legs: int) -> None:
        self.legs = legs


class Dog(Animal):
    def __init__(self) -> None:
        super().__init__(4)


class Snake(Animal):
    def __init__(self) -> None:
        super().__init__(0)


def classify(a: Animal) -> str:
    match a:  # tpyc: ok
        case Dog():
            return "dog"
        case Snake():
            return "snake legs=" + str(a.legs)
        case Animal():
            return "animal legs=" + str(a.legs)


def main() -> None:
    print(classify(Dog()))
    print(classify(Snake()))
    print(classify(Animal(6)))


main()
