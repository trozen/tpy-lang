# Sibling of `dyn_value_to_ptr/` (identity case): covers the
# protocol-to-parent-@dynamic-protocol upcast variant of the same coercion.
from typing import Protocol
from tpy import Int32, Ptr, dynamic


@dynamic
class Pet(Protocol):
    def name(self) -> Int32: ...


@dynamic
class NamedPet(Pet, Protocol):
    def label(self) -> Int32: ...


class Cat(NamedPet):
    tag: Int32
    def __init__(self, t: Int32) -> None:
        self.tag = t
    def name(self) -> Int32:
        return self.tag
    def label(self) -> Int32:
        return self.tag + 100


def takes_pet_ptr(p: Ptr[Pet]) -> Int32:
    return p.name()


def forward(np: NamedPet) -> Int32:
    return takes_pet_ptr(np)


def main() -> None:
    c = Cat(7)
    print(forward(c))


main()
