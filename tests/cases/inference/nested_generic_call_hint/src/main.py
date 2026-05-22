# LHS hints flow inward through nested generic function calls. Each
# call's type params are seeded from the outer hint before any arg is
# analyzed, so the inner constructor sees the right contextual hint
# on its first pass.
from typing import Protocol
from tpy import dynamic
from tplib import Box, Rc


@dynamic
class Greeter(Protocol):
    def greet(self) -> str: ...


class Cat:
    name: str
    def __init__(self, name: str) -> None:
        self.name = name
    def greet(self) -> str:
        return self.name


def main() -> None:
    # Outer Rc.new + inner Box constructor chain.
    rc: Rc[Box[Greeter]] = Rc.new(Box(Cat("Whiskers")))  # tpyc: type(Rc[Box[Greeter]])
    print(rc.get().get().greet())

    # Same chain, second instance -- exercises the seed across repeated
    # call sites within one function body.
    rc2: Rc[Box[Greeter]] = Rc.new(Box(Cat("Mittens")))  # tpyc: type(Rc[Box[Greeter]])
    print(rc2.get().get().greet())


main()
