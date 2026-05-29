# Optional[@dynamic protocol] is allowed at a parameter position: it lowers
# to a `const Pet*` borrow (may be nullptr). `is None` narrows, isinstance(p,
# Sub) dispatches via dynamic_cast on the underlying pointer, and a non-None
# value virtual-dispatches the protocol method.
from typing import Protocol, Optional
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def name(self) -> str:
        return self.tag

    def bark(self) -> str:
        return "woof " + self.tag


class Fish(Pet):
    def name(self) -> str:
        return "fish"


def greet(p: Optional[Pet]) -> str:
    if p is None:                 # tpyc: ok
        return "<none>"
    if isinstance(p, Dog):        # tpyc: ok
        return p.bark()
    return p.name()


def describe(p: Optional[Pet]) -> str:
    # isinstance directly on Optional[Pet] without a prior `is None` narrow:
    # narrows to the subclass (Dog), None casts to nullptr -> False.
    if isinstance(p, Dog):        # tpyc: ok
        return p.bark()
    return "not-dog"


def main() -> None:
    print(greet(Dog("rex")))
    print(greet(Fish()))
    print(greet(None))
    print(describe(Dog("fido")))
    print(describe(Fish()))
    print(describe(None))


main()
