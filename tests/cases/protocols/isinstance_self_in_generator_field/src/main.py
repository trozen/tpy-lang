# isinstance(self, Sub) in a generator body where a subclass-only field is
# read AFTER a suspension inside the narrowed block. The narrowed binding is
# a C++ local that does not survive the yield, so it must be re-established
# at the resume case from the frame field (regression: post-suspension access
# fell back to the base type -> 'Pet has no member breed').
from typing import Protocol, Iterator
from tpy import dynamic


@dynamic
class Tagged(Protocol):
    pass


class Pet(Tagged):
    name: str

    def __init__(self, nm: str) -> None:
        self.name = nm

    def describe(self) -> Iterator[str]:
        if isinstance(self, Dog):  # tpyc: ok
            yield "kind:dog"
            yield self.breed
            yield self.breed + "/" + self.name
        else:
            yield "kind:pet"
            yield self.name


class Dog(Pet):
    breed: str

    def __init__(self, nm: str, breed: str) -> None:
        super().__init__(nm)
        self.breed = breed


def main() -> None:
    d = Dog("rex", "lab")
    for s in d.describe():
        print(s)
    p = Pet("generic")
    for s in p.describe():
        print(s)


main()
