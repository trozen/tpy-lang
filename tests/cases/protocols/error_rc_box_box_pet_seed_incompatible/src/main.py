# Pins that the record-constructor bidir-hint seed does NOT suppress a
# real conformance mismatch deeper in a nested chain. Even though the
# outer Rc[Box[Box[Pet]]] hint propagates inward through the constructor
# args (seeding Pet at the innermost Box's T position), NotAPet still
# lacks the required `name(self) -> str` method, so the conformance
# check must still fire.
from typing import Protocol
from tpy import dynamic
from tplib import Box, Rc


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class NotAPet:
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    # No `name(self) -> str` -- doesn't conform to Pet.


def main() -> None:
    r: Rc[Box[Box[Pet]]] = Rc.new(Box(Box(NotAPet("Rex"))))  # tpyc: error(/does not conform|does not satisfy|Type mismatch/)
    print(r.get().get().get().name())


main()
