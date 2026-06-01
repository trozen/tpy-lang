# Regression: union param on a readonly GENERATOR method, discriminant-only
# (deep-const verdict). Frame field, factory signature, body, and call site must
# all use the const pointer-variant; before the fix the factory field/signature
# were mutable while the call site/body were const.
from typing import Iterator


class Dog:
    def __init__(self) -> None:
        pass


class Cat:
    def __init__(self) -> None:
        pass


class Zoo:
    tag: int

    def __init__(self) -> None:
        self.tag = 7

    def codes(self, a: Dog | Cat) -> Iterator[int]:
        yield self.tag
        if isinstance(a, Dog):
            yield 1
        else:
            yield 2


def main() -> None:
    z = Zoo()
    pet: Dog | Cat = Dog()
    for v in z.codes(pet):
        print(v)
    other: Dog | Cat = Cat()
    for v in z.codes(other):
        print(v)


main()
