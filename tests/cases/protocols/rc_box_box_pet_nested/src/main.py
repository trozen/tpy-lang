# Three-level nested chain Rc[Box[Box[Pet]]] = Rc.new(Box(Box(Dog(...)))).
# Inward LHS-hint propagation threads through both function calls (Rc.new)
# and record-constructor calls (the outer/middle Box), so each layer's
# T binding falls into place on its first analysis pass: outer Rc.new's
# value param seeds T=Box[Box[Pet]]; the middle Box constructor seeds its
# value param T=Box[Pet] from that hint; the inner Box constructor seeds
# T=Pet, and Box's existing record-construction LHS-hint preference flips
# its T from Dog to Pet for the Adapter wrap.
from typing import Protocol
from tpy import dynamic
from tplib import Box, Rc


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog:
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label


def main() -> None:
    r: Rc[Box[Box[Pet]]] = Rc.new(Box(Box(Dog("Rex"))))  # tpyc: type(Rc[Box[Box[Pet]]])
    print(r.get().get().get().name())


main()
