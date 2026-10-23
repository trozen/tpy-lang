# Two variants match the declared result, so the probe hints no argument by
# either: both `Box(Dog(..))` stay `Box[Dog]`, which no variant takes. Position
# 1 hinted from the first-declared variant would compile the call
# (BUGS.md#overload-arg-ctor-not-retyped-at-winner).
from typing import Protocol
from tpy import dynamic, Own, dispatch
from tplib import Box


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog:
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label


class Result:
    pass


@dispatch
def f(x: Own[Box[Pet]], y: Own[Box[Pet]]) -> Own[Result]:
    return Result()


@dispatch
def f(x: Own[Box[Pet]], y: str) -> Own[Result]:
    return Result()


def main() -> None:
    r: Result = f(Box(Dog("Rex")), Box(Dog("Spot")))  # tpyc: error(/No matching overload/)
    print(r)


main()
