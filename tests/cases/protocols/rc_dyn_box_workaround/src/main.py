# Rc[Box[Pet]] two-level composition: Box owns the dyn-protocol payload,
# Rc shares the Box. Three heap allocations total.
#
# The Box[Pet] LHS hint doesn't propagate through Rc.new's nested-generic
# inference, so the Box must be constructed on its own line with an
# explicit annotation; see `error_rc_box_pet_nested`.
from typing import Protocol
from tpy import dynamic
from tplib import Box, Rc


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Parrot(Pet):
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label


class Dog:
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label


def main() -> None:
    b1: Box[Pet] = Box(Parrot("Polly"))
    r1: Rc[Box[Pet]] = Rc.new(b1)
    r1_share = r1.clone()

    b2: Box[Pet] = Box(Dog("Rex"))
    r2: Rc[Box[Pet]] = Rc.new(b2)

    print(r1.get().get().name())
    print(r1_share.get().get().name())
    print(r2.get().get().name())


main()
