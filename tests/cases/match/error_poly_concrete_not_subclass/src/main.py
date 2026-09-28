# On a CONCRETE polymorphic-class subject, a class pattern naming a type
# that is not a subclass of the subject root can never match -- rejected.
from typing import Protocol
from tpy import dynamic


@dynamic
class Tag(Protocol):
    pass


class Animal(Tag):
    def __init__(self) -> None:
        pass


class Dog(Animal):
    def __init__(self) -> None:
        super().__init__()


class Widget:
    def __init__(self) -> None:
        pass


def classify(a: Animal) -> str:
    match a:
        case Dog():
            return "dog"
        case Widget():  # tpyc: error(/'Widget' is not a subclass of 'Animal'/)
            return "widget"
        case _:
            return "?"


def main() -> None:
    print(classify(Dog()))


main()
