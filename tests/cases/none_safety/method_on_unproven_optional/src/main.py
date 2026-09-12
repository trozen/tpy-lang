# A method call on an UNPROVEN Optional receiver null-checks the pointer at
# runtime. Two receiver flavors join the plain-record one: a GENERIC method,
# whose template args ride the checked deref, and a @dynamic-protocol pointee,
# where the check is pointee-blind.
from typing import Protocol

from tpy import int32, dynamic


class Bag:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def conv[T](self, x: T) -> T:
        return x


@dynamic
class Pet(Protocol):
    def sound(self) -> int32: ...


class Dog(Pet):
    def sound(self) -> int32:
        return 7


def generic_on_optional(b: Bag | None) -> int32:
    # targs ride the checked deref
    return b.conv(3)           # tpyc: warning(/Potential None access/)


def dyn_on_optional(p: Pet | None) -> int32:
    # a @dynamic pointee -- the check is pointee-blind
    return p.sound()           # tpyc: warning(/Potential None access/)


def main() -> None:
    print(generic_on_optional(Bag(1)))
    d = Dog()
    print(dyn_on_optional(d))


main()
