# A Box of a class that matches a @dynamic protocol only structurally does
# not convert to a Box of the protocol: the class does not inherit it
# (BUGS.md#overload-arg-ctor-not-retyped-at-winner).
from typing import Protocol
from tpy import dynamic, Own, StrView
from tplib import Box


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog:
    label: str

    def __init__(self, label: str) -> None:
        self.label = label

    def name(self) -> StrView:
        return self.label


def take(x: Own[Box[Pet]]) -> None:
    print(x.get().name())


def make() -> Own[Box[Dog]]:
    return Box(Dog("Rex"))


def main() -> None:
    # Dog conforms to Pet structurally, so Box[Dog] is no Box[Pet].
    take(make())  # tpyc: error(/Type mismatch in argument 'x': expected Box\[Pet\], got Box\[Dog\]/)


main()
