# A subclass RVALUE into a pointer-variant union slot still rejects: only an
# address binds there, and a temporary would slice into the base's value slot.
class Pet:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Dog(Pet):
    def __init__(self, name: str) -> None:
        super().__init__(name)


class Cat(Pet):
    def __init__(self, name: str) -> None:
        super().__init__(name)


def main() -> None:
    p: Pet | Cat = Dog("x")  # tpyc: warning(/upcast narrows/) error(/not yet supported/)
    if isinstance(p, Pet):
        print(p.name)


main()
