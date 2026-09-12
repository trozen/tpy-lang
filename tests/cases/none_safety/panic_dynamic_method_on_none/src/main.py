# The null path of a @dynamic-protocol method call on an unproven Optional
# receiver: the check is pointee-blind, so a None receiver panics before any
# dispatch happens.
from typing import Protocol

from tpy import int32, dynamic


@dynamic
class Pet(Protocol):
    def sound(self) -> int32: ...


class Dog(Pet):
    def sound(self) -> int32:
        return 7


def dyn_on_optional(p: Pet | None) -> int32:
    return p.sound()  # tpyc: warning(/Potential None access/)


def main() -> None:
    d = Dog()
    print(dyn_on_optional(d))
    p: Pet | None = None
    print(dyn_on_optional(p))


main()
