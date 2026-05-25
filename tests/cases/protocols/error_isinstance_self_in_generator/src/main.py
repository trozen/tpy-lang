# isinstance(self, Sub) inside a generator method is rejected by sema.
# Codegen doesn't yet support it: the generator frame captures __self as
# `const T&` and the goto-based state-machine dispatch doesn't compose
# with the if-init dynamic_cast form.
from typing import Protocol, Iterator
from tpy import dynamic


@dynamic
class Tagged(Protocol):
    pass


class Pet(Tagged):
    _name: str

    def __init__(self, n: str) -> None:
        self._name = n

    def names(self) -> Iterator[str]:
        if isinstance(self, Dog):  # tpyc: error(/not yet supported inside a generator/)
            yield "dog:" + self._name
        else:
            yield "pet:" + self._name


class Dog(Pet):
    def __init__(self, n: str) -> None:
        super().__init__(n)


def main() -> None:
    pass


main()
