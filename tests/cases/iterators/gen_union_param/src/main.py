# Regression: a non-value union (Dog | Cat) passed to a resumable generator
# factory must use the pointer-variant borrow form; also matches on it across a yield.
from typing import Iterator


class Dog:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def sound(self) -> str:
        return "woof"


class Cat:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def sound(self) -> str:
        return "meow"


def describe(a: Dog | Cat) -> Iterator[str]:
    yield "start"
    match a:
        case Dog():
            yield "dog:" + a.sound()
        case Cat():
            yield "cat:" + a.sound()


def main() -> None:
    # First call passes a temporary, second a named local -- both must stay live.
    for s in describe(Dog("rex")):
        print(s)
    pet: Dog | Cat = Cat("tom")
    for s in describe(pet):
        print(s)


main()
