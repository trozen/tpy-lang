# typing.cast(T, x) on Any uses exact-typeid matching: it does not walk
# the inheritance chain. Casting an Any holding a Dog to Animal panics
# even though Dog is a subclass of Animal. Pinned per the design doc's
# "v1 is exact-type only" rule.

from typing import Any, cast


class Animal:
    def __init__(self, name: str) -> None:
        self.name = name


class Dog(Animal):
    def __init__(self, name: str) -> None:
        Animal.__init__(self, name)


def main() -> None:
    a: Any = Dog("Rex")
    parent = cast(Animal, a)
    print(parent.name)


main()
