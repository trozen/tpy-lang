# Regression guard: the concrete overload wins over a protocol overload
# that the same argument also satisfies, regardless of declaration order.
#
# Before the tier-ranked redesign, first-pass overload resolution returned
# the first strictly-matching candidate, so declaring the protocol overload
# first (as below) would have silently picked it -- the `describe(Dog)`
# specialisation would never fire. This test pins the fix in place: reorder
# the stubs and the output must not change.
from typing import Protocol
from tpy import dispatch


class Animal(Protocol):
    def name(self) -> str: ...


class Dog:
    def __init__(self) -> None:
        pass

    def name(self) -> str:
        return "Rex"


@dispatch
def describe(x: Animal) -> str:  # tpyc: ok
    return "animal: " + x.name()


@dispatch
def describe(x: Dog) -> str:  # tpyc: ok
    return "dog: " + x.name()


def main() -> None:
    d = Dog()
    print(describe(d))


main()
