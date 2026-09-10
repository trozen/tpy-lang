# A reference union compares at a borrow position
# (union/reference_union_borrow_eq) without CONFORMING to anything, so a dict
# key, a set element, `in` and `sorted` keep rejecting
# (BUGS.md#value-union-no-equatable-conformance owns those).
from tpy import Int32


class Dog:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __eq__(self, other: "Dog") -> bool:
        return self.n == other.n


class Cat:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __eq__(self, other: "Cat") -> bool:
        return self.n == other.n


type Pet = Dog | Cat


def by_pet() -> Int32:
    d: dict[Pet, Int32] = {}  # tpyc: error(/cannot be used as a dict key/)
    return len(d)


def main() -> None:
    print(by_pet())


main()
