# Rc[Box[Pet]] -- interim workaround for shared-ownership of @dynamic
# protocol values until Rc gains direct Rc[P] support (TODO.md).
#
# Two layers compose: Box[Pet] handles the abstract-storage shape (heap-
# allocates an Adapter<Pet, ConcreteT> for structural conformers, or the
# concrete directly for inheritance); Rc wraps the Box for shared
# ownership. Total: two heap allocations -- one for Rc's cell containing
# the Box value, one for the Box's pet allocation. More than the planned
# Rust-style co-located Rc[Pet] (one block) but works today with shipped
# Box[P] machinery.
#
# Note: the LHS-hint preference that lets `Box[Pet] = Box(Parrot(...))`
# work doesn't propagate through Rc.new's nested-generic inference, so
# the Box must be constructed on a separate line with explicit Box[Pet]
# annotation first; THEN passed to Rc.new.
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
    # Inheritance path conformer wrapped in Box[Pet], then shared via Rc.
    b1: Box[Pet] = Box(Parrot("Polly"))
    r1: Rc[Box[Pet]] = Rc.new(b1)
    r1_share = r1.clone()  # share refcount; both handles point at the same Box[Pet]

    # Structural path conformer.
    b2: Box[Pet] = Box(Dog("Rex"))
    r2: Rc[Box[Pet]] = Rc.new(b2)

    print(r1.get().get().name())        # Rc -> Box -> Pet, virtual dispatch
    print(r1_share.get().get().name())  # same data through cloned Rc
    print(r2.get().get().name())


main()
