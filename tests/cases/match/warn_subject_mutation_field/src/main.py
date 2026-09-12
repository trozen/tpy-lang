# Reassigning a union FIELD subject while arm bindings borrow it warns
# (the bindings would dangle). Runtime takes the non-mutating arm.
from tpy import int32


class Dog:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Cat:
    age: int32

    def __init__(self, age: int32) -> None:
        self.age = age


class Holder:
    pet: Dog | Cat

    def __init__(self) -> None:
        self.pet = Cat(3)


def poke(h: Holder) -> None:
    match h.pet:
        case Dog(name=n):
            h.pet = Cat(9)  # tpyc: warning(/'h.pet' is mutated in this arm while pattern bindings borrow/)
            print(n)
        case Cat(age=a):
            print("cat", a)


def main() -> None:
    poke(Holder())


main()
