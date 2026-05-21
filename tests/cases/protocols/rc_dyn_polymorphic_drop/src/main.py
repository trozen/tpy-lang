# Polymorphic destruction through Rc[@dynamic P]: ~Parrot must fire, not
# ~Pet -- heap_release dispatches through P's virtual destructor.
from typing import Protocol
from tpy import dynamic
from tplib import Rc


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Parrot(Pet):
    label: str
    def __init__(self, label: str) -> None:
        print(f"Parrot({label}) ctor")
        self.label = label
    def name(self) -> str:
        return self.label
    def __del__(self) -> None:
        print(f"~Parrot({self.label})")


def make_rc() -> None:
    r: Rc[Pet] = Rc.new(Parrot("Polly"))
    print(r.get().name())


def main() -> None:
    print("before make_rc")
    make_rc()
    print("after make_rc")


main()
