# Pins that the bidir-hint seed does NOT suppress a real conformance
# mismatch in a nested generic arg. The LHS hint Rc[Box[Pet]] would seed
# T=Box[Pet] for Rc.new and propagate Box[Pet] as the inner Box(...) hint;
# but the actual arg is Box(NotAPet(...)) where NotAPet has no method that
# would let it conform to Pet. The seed-driven analysis must still surface
# a conformance error, not silently accept it.
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
    # No `name(self) -> str` -- NotAPet does not conform to Pet.


def main() -> None:
    r: Rc[Box[Pet]] = Rc.new(Box(NotAPet("Rex")))  # tpyc: error(/does not conform|does not satisfy|Type mismatch/)
    print(r.get().get().name())


main()
