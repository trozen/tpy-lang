# @dynamic protocol -> str with covariant returns (StrView, String).
# Tests both adapter path (structural conformance) and direct inheritance.
from tpy import dynamic, StrView, String
from typing import Protocol


@dynamic
class Named(Protocol):
    def name(self) -> str: ...


# Structural conformance (adapter wraps the call)
class Dog:
    _name: str

    def __init__(self, n: str):
        self._name = n

    def name(self) -> StrView:
        return self._name


class Cat:
    _name: str

    def __init__(self, n: str):
        self._name = n

    def name(self) -> String:
        return self._name


# Direct inheritance (override must match vtable signature)
class Bird(Named):
    _name: str

    def __init__(self, n: str):
        self._name = n

    def name(self) -> StrView:
        return self._name


class Fish(Named):
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
    greet(Fish("Nemo"))


main()
