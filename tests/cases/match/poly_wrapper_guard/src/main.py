# match/case polymorphic dispatch reached through an owning wrapper's
# deref view (Box[Pet]) plus guards. The arms dispatch on the deref
# payload's dynamic type (the Box itself is never narrowed), and a failed
# guard falls through to the next arm via the goto-label mechanism.
from typing import Protocol
from tpy import dynamic
from tplib import Box


@dynamic
class Pet(Protocol):
    def legs(self) -> int: ...


class Dog(Pet):
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def legs(self) -> int:
        return 4


class Bird(Pet):
    def __init__(self) -> None:
        pass

    def legs(self) -> int:
        return 2


def describe(b: Box[Pet]) -> str:
    match b:  # tpyc: ok
        case Dog(n=k) if k > 0:
            return "dog+ " + str(k) + " legs=" + str(b.legs())
        case Dog():
            return "dog0 legs=" + str(b.legs())
        case _:
            return "other legs=" + str(b.legs())


def main() -> None:
    print(describe(Box(Dog(5))))
    print(describe(Box(Dog(0))))
    print(describe(Box(Bird())))


main()
