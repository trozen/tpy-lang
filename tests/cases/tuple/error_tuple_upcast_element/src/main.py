# A subclass element copied into a base-class tuple element is refused when
# a `Base.__init__` takes the subclass: C++ would run that initializer instead
# of the copy (BUGS.md#tuple-element-upcast-copy-refused).
from tpy import Own, int32


class Base:
    n: int32

    def __init__(self, other: "Child") -> None:
        self.n = other.n + 100


class Child(Base):
    def __init__(self, n: int32) -> None:
        self.n = n


def widen(t: tuple[Child, int32]) -> Own[tuple[Base, int32]]:
    # the copy of the `Child` element into the `Base` element is the refused shape
    return t  # tpyc: error(/copying a 'Child' object into a 'Base' tuple element is not supported/)


def main() -> None:
    t = widen((Child(7), 0))
    print(t[0].n)


main()
