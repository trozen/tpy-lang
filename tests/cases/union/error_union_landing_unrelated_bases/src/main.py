# A record deriving from two unrelated union members lands in neither: C++
# would have to pick one, so the argument is refused in sema
# (BUGS.md#union-unrelated-bases-refused).


class Pet:
    p: int

    def __init__(self) -> None:
        self.p = 1


class Animal:
    a: int

    def __init__(self) -> None:
        self.a = 2


class Dog(Pet, Animal):
    def __init__(self) -> None:
        Pet.__init__(self)
        Animal.__init__(self)


def take(x: Pet | Animal) -> None:
    print(isinstance(x, Pet))


def main() -> None:
    take(Dog())  # tpyc: error(/'Dog' derives from both 'Animal' and 'Pet'/)


main()
