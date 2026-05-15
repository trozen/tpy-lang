# Box[P].take() for abstract @dynamic P: must avoid declaring a local
# of abstract T. Returns Own[T] (= unique_ptr<P> for abstract P) via the
# tpy::transfer_ownership helper, which wraps the heap pointer directly
# without going through a T-typed local.
#
# We chain take() into Box(...) instead of storing in an intermediate
# `pet: Own[Pet]` local + calling pet.name(), because method access
# through an Own[abstract_P] value is a separate gap (TODO.md).
from typing import Protocol
from tpy import dynamic
from tplib import Box


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Parrot(Pet):
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label


def main() -> None:
    b1: Box[Pet] = Box(Parrot(label="Polly"))
    b2: Box[Pet] = Box(b1.take())
    print(b2.get().name())


main()
