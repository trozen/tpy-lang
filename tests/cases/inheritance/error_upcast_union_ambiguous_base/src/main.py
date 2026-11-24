# A subclass of TWO union members bound into the pointer-variant union is
# rejected in sema: the address could land in either member
# (BUGS.md#union-unrelated-bases-refused).
class Pet:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Animal:
    legs: int

    def __init__(self, legs: int) -> None:
        self.legs = legs


class Dog(Pet, Animal):
    def __init__(self, name: str) -> None:
        Pet.__init__(self, name)
        Animal.__init__(self, 4)


def main() -> None:
    d = Dog("rex")
    p: Pet | Animal = d  # tpyc: error(/'Dog' derives from both 'Animal' and 'Pet'/)
    if isinstance(p, Pet):
        print(p.name)


main()
