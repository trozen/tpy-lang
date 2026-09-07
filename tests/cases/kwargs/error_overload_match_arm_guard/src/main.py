# A `match` over the union parameter of an `@overload` implementation, with an
# `if` guard on the arm: not lowered yet, so the case pins the reject.
from typing import overload


class Dog:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Cat:
    lives: int

    def __init__(self, lives: int) -> None:
        self.lives = lives


@overload
def describe(a: Dog, k: int) -> str: ...


@overload
def describe(a: Cat, k: int) -> str: ...


def describe(a: Dog | Cat, k: int) -> str:
    match a:  # tpyc: error(/match.overload_fold_guard/)
        case Dog(name=n) if k > 0:
            return n
        case _:
            return "other"


def main() -> None:
    print(describe(Dog("rex"), 1))


main()
