# Regression: a non-value union param on a resumable generator METHOD (vs the
# free-function gen_union_param) must use the pointer-variant borrow form.
# `bump` mutates self so the method is not readonly (the readonly-method union
# param shape is tracked separately as a known bug).
from typing import Iterator
from tpy import readonly


class Dog:
    def __init__(self) -> None:
        pass

    def speak(self) -> str:
        return "woof"


class Cat:
    def __init__(self) -> None:
        pass

    def speak(self) -> str:
        return "meow"


class Zoo:
    seen: int

    def __init__(self) -> None:
        self.seen = 0

    def voices(self, a: Dog | Cat) -> Iterator[str]:
        self.seen += 1
        yield "start"
        match a:
            case Dog():
                yield a.speak()
            case Cat():
                yield a.speak()

    def names(self, a: readonly[Dog | Cat]) -> Iterator[str]:
        self.seen += 1
        yield "ro"
        match a:
            case Dog():
                yield "dog"
            case Cat():
                yield "cat"


def main() -> None:
    z = Zoo()
    pet: Dog | Cat = Dog()
    for s in z.voices(pet):
        print(s)
    other: Dog | Cat = Cat()
    for s in z.names(other):
        print(s)
    print(z.seen)


main()
