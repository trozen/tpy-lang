# A later polymorphic arm whose type is a subclass-or-equal of an earlier
# arm's type is unreachable: the earlier (broader) dynamic_cast already
# caught it. Sema rejects the shadowed arm.
from typing import Protocol
from tpy import dynamic


@dynamic
class Tag(Protocol):
    pass


class Animal(Tag):
    def __init__(self) -> None:
        pass


class Mammal(Animal):
    def __init__(self) -> None:
        super().__init__()


class Dog(Mammal):
    def __init__(self) -> None:
        super().__init__()


def f(a: Animal) -> str:
    match a:
        case Mammal():
            return "m"
        case Dog():  # tpyc: error(/unreachable case for 'Dog'.*Mammal/)
            return "d"
        case _:
            return "x"


def main() -> None:
    print(f(Dog()))


main()
