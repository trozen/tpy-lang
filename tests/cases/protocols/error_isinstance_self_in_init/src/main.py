# isinstance(self, Sub) inside __init__/__del__ is rejected because the C++
# ctor/dtor dynamic-type rule makes the check always-False (diverging from
# CPython, which sees the derived type during construction).
from typing import Protocol
from tpy import dynamic


@dynamic
class Tagged(Protocol):
    pass


class Pet(Tagged):
    _name: str
    _tag: str

    def __init__(self, n: str) -> None:
        self._name = n
        if isinstance(self, Dog):  # tpyc: error(/isinstance\(self/)
            self._tag = "dog"
        else:
            self._tag = "other"


class Dog(Pet):
    def __init__(self, n: str) -> None:
        super().__init__(n)


def main() -> None:
    pass


main()
