# Regression: a @dynamic protocol local reassigned inside __init__ hoists its
# rebind slot to the ctor-body top -- the THIR ctor emit must drain hoist_lines
# (else `__slot_N.emplace` references an undeclared slot -> invalid C++). The
# ctor is fieldless so its body routes through THIR (a field write would defer).
from tpy import dynamic
from typing import Protocol


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def name(self) -> str:
        return "Rex"


class Cat(Pet):
    def name(self) -> str:
        return "Meow"


class Announcer:
    def __init__(self) -> None:
        pet: Pet = Dog()
        print(pet.name())
        pet = Cat()
        print(pet.name())


def main() -> None:
    Announcer()


main()
