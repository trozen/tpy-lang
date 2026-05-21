# Rc[@dynamic P] with an inheritance conformer (Parrot inherits Pet);
# Covariant[T] uplifts Rc[Parrot] -> Rc[Pet] at the assignment.
from typing import Protocol
from tpy import dynamic
from tplib import Rc


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Parrot(Pet):
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label
    def __del__(self) -> None:
        print(f"~Parrot({self.label})")


def main() -> None:
    r: Rc[Pet] = Rc.new(Parrot("Polly"))
    print(r.get().name())


main()
