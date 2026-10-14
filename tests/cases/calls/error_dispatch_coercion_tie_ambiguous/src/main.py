# A crossed tie the conversion stage reaches is ambiguous, though CPython runs
# the first declared (docs/LANGUAGE_FEATURES.md "Overloading": a tie in either pass).
from tpy import dispatch, int8, int32, int64


class Animal:
    hits: int32

    def __init__(self) -> None:
        self.hits = 0


class Dog(Animal):
    def __init__(self) -> None:
        super().__init__()


@dispatch
def t(x: Dog, y: Animal, z: int64) -> str:
    x.hits += 1
    return "dog_animal"


@dispatch
def t(x: Animal, y: Dog, z: int64) -> str:
    y.hits += 1
    return "animal_dog"


def main() -> None:
    n8: int8 = 1
    print(t(Dog(), Dog(), n8))  # tpyc: error(/Ambiguous overload for 't'/)


main()
