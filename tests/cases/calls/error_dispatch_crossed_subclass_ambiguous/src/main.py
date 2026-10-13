# A crossed tie (each variant exact on one argument, subclass on the other)
# is ambiguous: declaration order never breaks a tie (docs/OVERLOAD_DESIGN.md
# "Ambiguity"). Adding a `t(x: Dog, y: Dog)` variant resolves it.
from tpy import dispatch, int32


class Animal:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0


class Dog(Animal):
    def __init__(self) -> None:
        super().__init__()


@dispatch
def t(x: Dog, y: Animal) -> str:
    x.hits += 1
    return "dog,animal"


@dispatch
def t(x: Animal, y: Dog) -> str:
    y.hits += 1
    return "animal,dog"


def main() -> None:
    d = Dog()
    e = Dog()
    print(t(d, e))  # tpyc: error(/Ambiguous overload for 't'/)


main()
