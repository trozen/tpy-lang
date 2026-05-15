# Box[P].take() for abstract @dynamic P: must avoid declaring a local
# of abstract T. Returns Own[T] (= unique_ptr<P> for abstract P) via the
# tpy::transfer_ownership helper, which wraps the heap pointer directly
# without going through a T-typed local. Body-side method access through
# an Own[P] is now supported (`protocol_dynamic_own_param` covers it),
# but storing the take()'d unique_ptr in an `Own[Pet]` local annotation
# isn't a thing -- Own[T] is only valid in param/return position. So we
# still chain take() straight into the next Box(...).
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
