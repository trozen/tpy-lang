# Three-level nested chain Rc[Box[Box[Pet]]] = Rc.new(Box(Box(Dog(...))))
# pins the next limitation: bidir-hint inference threads through function
# calls (Rc.new) but not through record-constructor args. The outer Box
# receives a Box[Box[Pet]] hint, but its own constructor analyzes the inner
# Box(Dog(...)) without propagating any contextual hint -- and the existing
# Box LHS-hint preference (`_apply_dyn_hint_at_position`) only fires when
# the hint position is a @dynamic protocol directly (Pet), not a generic
# wrapper of one (Box[Pet]). So inner Box(Dog) infers T=Dog, outer Box
# infers T=Box[Dog], and the assignment fails.
#
# Lifting this needs record-constructor-side bidir-hint seeding (parallel
# to what calls.py already does for function calls); see TODO.md.
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
    r: Rc[Box[Box[Pet]]] = Rc.new(Box(Box(Dog("Rex"))))  # tpyc: error(/Type mismatch.*Rc\[Box\[Box\[Pet\]\]\].*Rc\[Box\[Box\[Dog\]\]\]/)
    print(r.get().get().get().name())


main()
