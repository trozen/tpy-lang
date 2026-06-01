# A polymorphic subject ranges over an open set of subclasses, so a match
# without an unconditional catch-all (wildcard or root-type arm) is never
# exhaustive -- sema warns and the match falls through when no arm matches.
from typing import Protocol
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def speak(self) -> str: ...


class Dog(Pet):
    def __init__(self) -> None:
        pass

    def speak(self) -> str:
        return "woof"


class Cat(Pet):
    def __init__(self) -> None:
        pass

    def speak(self) -> str:
        return "meow"


def describe(p: Pet) -> str:
    match p:  # tpyc: warning(/non-exhaustive match.*no unconditional catch-all/)
        case Dog():
            return "dog"
        case Cat():
            return "cat"
    return "fallthrough"


def main() -> None:
    print(describe(Dog()))
    print(describe(Cat()))


main()
