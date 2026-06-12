# Overwriting an OWNER FIELD on the subject path warns: replacing
# o.inner replaces the storage o.inner.pet's bindings borrow (fields
# have no slot model). Runtime takes the non-mutating arm.
from tpy import Int32


class Dog:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Cat:
    age: Int32

    def __init__(self, age: Int32) -> None:
        self.age = age


class Holder:
    pet: Dog | Cat

    def __init__(self) -> None:
        self.pet = Cat(3)


class Outer:
    inner: Holder

    def __init__(self) -> None:
        self.inner = Holder()


def poke(o: Outer) -> None:
    match o.inner.pet:
        case Dog(name=n):
            o.inner = Holder()  # tpyc: warning(/'o.inner.pet' is mutated in this arm while pattern bindings borrow/)
            print(n)
        case Cat(age=a):
            print("cat", a)


def main() -> None:
    poke(Outer())


main()
