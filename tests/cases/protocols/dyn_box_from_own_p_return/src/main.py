# Forwarding shortcut: when the arg to Box[P] is itself an Own[P] value
# (here, a method param `initial: Own[Pet]` forwarded to `Box(initial)`),
# the value is already unique_ptr<Pet> and must be forwarded without
# Adapter wrapping. Adapter<P, P> would contain an abstract P field --
# ill-formed.
from typing import Protocol
from tpy import dynamic, Own
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


class Adopter:
    pet: Box[Pet]

    def __init__(self, initial: Own[Pet]) -> None:
        self.pet = Box(initial)


def main() -> None:
    a = Adopter(Parrot(label="Polly"))
    print(a.pet.get().name())


main()
