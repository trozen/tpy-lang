# Rc[@dynamic P] with a structural conformer (Cat does not inherit Pet);
# LHS-hint preference flips T at function inference, Phase 16 then wraps
# the Cat rvalue into Adapter<Pet, Cat> at the Rc.new call boundary.
from typing import Protocol
from tpy import dynamic
from tplib import Rc


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Cat:
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label
    def __del__(self) -> None:
        print(f"~Cat({self.label})")


def main() -> None:
    r: Rc[Pet] = Rc.new(Cat("Whiskers"))
    print(r.get().name())


main()
