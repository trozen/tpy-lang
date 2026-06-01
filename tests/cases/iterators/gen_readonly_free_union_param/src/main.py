# Regression: @readonly free-function generator with a union param -- factory
# field, signature, body, and call site all read the one deep-const verdict.
from typing import Iterator
from tpy import readonly


class Dog:
    def __init__(self) -> None:
        pass


class Cat:
    def __init__(self) -> None:
        pass


@readonly
def codes(a: Dog | Cat) -> Iterator[int]:
    yield 0
    if isinstance(a, Dog):
        yield 1
    else:
        yield 2


def main() -> None:
    pet: Dog | Cat = Dog()
    for v in codes(pet):
        print(v)


main()
