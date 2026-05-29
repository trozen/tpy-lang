# Own[Pet] parameter accepts a subclass/conformer rvalue: an inheritance
# conformer (Dog) and a structural conformer (Cat, wrapped in an Adapter) are
# both materialized into the std::unique_ptr<Pet> slot with dynamic type
# preserved, so p.name() virtual-dispatches.
from typing import Protocol
from tpy import dynamic, Own


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):                  # inheritance conformer
    def name(self) -> str:
        return "dog"


class Cat:                       # structural conformer, no inheritance
    label: str

    def __init__(self, label: str) -> None:
        self.label = label

    def name(self) -> str:
        return self.label


def adopt(p: Own[Pet]) -> Own[Pet]:  # tpyc: ok
    return p                         # consume by forwarding ownership out


def main() -> None:
    print(adopt(Dog()).name())       # inheritance rvalue
    print(adopt(Cat("felix")).name())  # structural rvalue -> Adapter


main()
