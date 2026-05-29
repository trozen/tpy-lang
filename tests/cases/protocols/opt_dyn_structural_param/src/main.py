# A structural conformer (Cat has name() but does not inherit Pet) passed to an
# Optional[@dynamic protocol] param is carried via an Adapter that inherits the
# base, so &adapter is a valid Pet* -- mirroring the bare-Pet& param path.
from typing import Protocol, Optional
from tpy import dynamic


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Cat:                       # structural conformer, no inheritance
    label: str

    def __init__(self, label: str) -> None:
        self.label = label

    def name(self) -> str:
        return self.label


def greet(p: Optional[Pet]) -> str:
    if p is None:
        return "<none>"
    return p.name()


def main() -> None:
    print(greet(Cat("whiskers")))   # structural rvalue -> Adapter temp -> &Adapter
    felix = Cat("felix")
    print(greet(felix))             # structural lvalue -> RefAdapter temp
    print(greet(None))


main()
