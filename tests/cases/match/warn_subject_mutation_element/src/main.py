# An invalidating container method on the subject's base while arm
# bindings borrow an element warns (realloc dangles the bindings).
# Runtime takes the non-mutating arm.
from tpy import Int32


class Dog:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Cat:
    age: Int32

    def __init__(self, age: Int32) -> None:
        self.age = age


def poke(xs: list[Dog | Cat]) -> None:
    match xs[0]:
        case Dog(name=n):
            xs.append(Cat(9))  # tpyc: warning(/'xs\[0\]' is mutated in this arm while pattern bindings borrow/)
            print(n)
        case Cat(age=a):
            print("cat", a)


def main() -> None:
    poke([Cat(4)])


main()
