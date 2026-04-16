# @overload methods with bodies directly (no trailing impl)
from typing import overload


class Animal:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    @overload
    def greet(self, x: int) -> str:  # tpyc: ok
        return self.name + " got " + str(x) + " treats"

    @overload
    def greet(self, x: str) -> str:  # tpyc: ok
        return self.name + " heard '" + x + "'"


def main() -> None:
    a = Animal("Rex")
    print(a.greet(3))
    print(a.greet("hello"))


main()
