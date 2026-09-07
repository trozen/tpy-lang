# A branch-first union declaration later reassigned from an rvalue needs a
# function-scope rebind slot, a placement the reseat rows do not spell, so
# `pet: Dog | Cat = c` is rejected.
from tpy import copy


class Dog:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


class Cat:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name


def show(c: Cat) -> None:
    pet: Dog | Cat = c  # tpyc: error(/decl.ptr_union_source/)
    if isinstance(pet, Cat):
        pet3 = copy(pet)
        pet3 = copy(pet)
        print(isinstance(pet3, Cat))


def main() -> None:
    show(Cat("m"))


main()
