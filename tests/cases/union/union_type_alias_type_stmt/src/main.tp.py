# Type alias using Python 3.12 'type' statement syntax
from tpy import Int32


class Dog:
    age: Int32

    def __init__(self, age: Int32) -> None:
        self.age = age


class Cat:
    age: Int32

    def __init__(self, age: Int32) -> None:
        self.age = age


type Pet = Dog | Cat


def describe(p: Pet) -> str:
    if isinstance(p, Dog):
        return "dog"
    assert isinstance(p, Cat)
    return "cat"


def main() -> None:
    d: Pet = Dog(Int32(3))
    c: Pet = Cat(Int32(5))
    print(describe(d))
    print(describe(c))

main()
