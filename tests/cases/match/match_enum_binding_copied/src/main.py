# An enum match binding is a free-copy scalar: copied at the arm, so mutating
# the subject storage (reassigning the union field) keeps the matched value.
from tpy import Int32
from enum import Enum


class Color(Enum):
    RED = 1
    BLUE = 2


class Dog:
    shade: Color

    def __init__(self, shade: Color) -> None:
        self.shade = shade


class Cat:
    age: Int32

    def __init__(self, age: Int32) -> None:
        self.age = age


class Holder:
    pet: Dog | Cat

    def __init__(self) -> None:
        self.pet = Dog(Color.RED)


def main() -> None:
    h = Holder()
    match h.pet:
        case Dog(shade=s):
            h.pet = Cat(9)  # destroys the Dog; s is a copy of the enum
            print(s == Color.RED)
        case Cat(age=a):
            print("cat", a)


main()
