# A type argument that is not a subtype of a class bound is rejected at the
# call site (nominal subtype satisfaction). Cat does not inherit Animal.
from tpy import Ptr, Int32, take_ptr


class Animal:
    def kind(self) -> Int32:
        return 1


class Cat:
    def kind(self) -> Int32:
        return 2


def as_animal[U: Animal](p: Ptr[U]) -> Ptr[Animal]:
    return p


def main() -> None:
    c = Cat()
    pa = as_animal[Cat](take_ptr(c))  # tpyc: error(/'Cat' does not satisfy bound 'Animal'/)
    print(pa.kind())


main()
