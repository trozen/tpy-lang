# Protocol -> str accepts implementations returning str, StrView, or String.
from typing import Protocol
from tpy import StrView, String


class Named(Protocol):
    def name(self) -> str: ...


class Dog:
    _name: str

    def __init__(self, n: str):
        self._name = n

    def name(self) -> str:
        return self._name


class Cat:
    _name: str

    def __init__(self, n: str):
        self._name = n

    def name(self) -> StrView:
        return self._name


class Bird:
    _name: str

    def __init__(self, n: str):
        self._name = n

    def name(self) -> String:
        return self._name


def greet(x: Named) -> None:
    print(x.name())


def main():
    greet(Dog("Rex"))
    greet(Cat("Whiskers"))
    greet(Bird("Tweety"))


main()
