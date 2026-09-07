# An `@overload` implementation that branches on a non-overloaded parameter
# BEFORE the `isinstance` test: not lowered yet, so the case pins the reject.
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
def judge(a: Dog, n: int) -> str: ...


@overload
def judge(a: Cat, n: int) -> str: ...


def judge(a: Dog | Cat, n: int) -> str:
    # The Dog stub's isinstance folds TRUE behind a still-dynamic branch, a
    # shape whose fold would drop the dynamic test.
    if n > 3:  # tpyc: error(/if.overload_true_after_dynamic/)
        return "big"
    elif isinstance(a, Dog):
        return a.name
    else:
        return str(a.lives)


def main() -> None:
    print(judge(Dog("rex"), 1))


main()
