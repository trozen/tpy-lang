# An `@overload` pair whose SHORT stub omits the trailing defaulted `bool`
# parameter: the body still reads that parameter in both specializations.
from typing import overload


class Dog:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Cat:
    lives: int

    def __init__(self, lives: int) -> None:
        self.lives = lives


@overload
def greet(a: Dog) -> str: ...


@overload
def greet(a: Cat, loud: bool) -> str: ...


def greet(a: Dog | Cat, loud: bool = False) -> str:
    # `loud` is a real parameter for the long stub and the default for the
    # short one; both bodies read it.
    suffix = "!" if loud else "."
    if isinstance(a, Dog):
        return a.name + suffix
    return str(a.lives) + suffix


def main() -> None:
    print(greet(Dog("rex")))
    print(greet(Cat(9), True))
    print(greet(Cat(3), False))


main()
