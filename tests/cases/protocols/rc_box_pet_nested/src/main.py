# Rc[Box[Pet]] from nested generic constructors. The LHS hint
# Rc[Box[Pet]] flows inward through Rc.new's generic inference to give
# the inner Box(Dog(...)) call a Box[Pet] hint, so Box's record-construction
# LHS-hint preference switches T from Dog to Pet on the first analysis pass.
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
    r: Rc[Box[Pet]] = Rc.new(Box(Dog("Rex")))  # tpyc: type(Rc[Box[Pet]])
    print(r.get().get().name())


main()
