# Polymorphic match capture (`case x:` binds the base type) and as-pattern
# (`case Dog() as d:` binds the narrowed subclass), over a const (readonly)
# borrow -- the cast and bindings stay const.
from typing import Protocol
from tpy import dynamic, readonly


@dynamic
class Pet(Protocol):
    @readonly
    def speak(self) -> str: ...


class Dog(Pet):
    def __init__(self) -> None:
        pass

    @readonly
    def speak(self) -> str:
        return "woof"


class Cat(Pet):
    def __init__(self) -> None:
        pass

    @readonly
    def speak(self) -> str:
        return "meow"


def describe(p: Pet) -> str:
    match p:  # tpyc: ok
        case Dog() as d:
            return "dog:" + d.speak()
        case other:
            return "other:" + other.speak()


def main() -> None:
    print(describe(Dog()))
    print(describe(Cat()))


main()
