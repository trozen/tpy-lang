# Rc[Box[Pet]] interim workaround -- nested form WITHOUT intermediate local
# fails today. Pins the limitation so future work fixing function-call
# LHS-hint propagation (TODO.md Rc[P] entry, gap 1) lifts this restriction.
#
# What works (see rc_dyn_box_workaround test):
#     b: Box[Pet] = Box(Dog("Rex"))     # explicit annotation flips Box's T to Pet
#     r: Rc[Box[Pet]] = Rc.new(b)        # Rc.new just wraps the already-typed Box
#
# What FAILS (this test):
#     r: Rc[Box[Pet]] = Rc.new(Box(Dog("Rex")))
#
# The LHS hint Rc[Box[Pet]] would need to flow INWARD through Rc.new's
# generic inference to give the inner Box(...) call a Box[Pet] hint that
# triggers Box's switch-T-to-Pet rule. Rc.new's inference is on the
# function-call path which lacks the LHS-hint preference (only the
# record-constructor path has it -- that's where Box benefits). So:
#   - Inner Box(Dog("Rex")) sees no hint -> infers T=Dog -> returns Box<Dog>
#   - Rc.new(Box<Dog>) infers T=Box[Dog] -> returns Own[Rc[Box[Dog]]]
#   - Assignment to Rc[Box[Pet]] fails: Box<Dog> structurally conforms to Pet
#     but the wrapping rule only fires inside Box's own inference, not here.
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
    r: Rc[Box[Pet]] = Rc.new(Box(Dog("Rex")))  # tpyc: error(/Type mismatch.*Rc\[Box\[Pet\]\].*Rc\[Box\[Dog\]\]/)
    print(r.get().get().name())


main()
