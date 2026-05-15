# Box[P] deref shortcut -- `box.name()` instead of `box.get().name()`.
# Exercises Box's Deref[T] protocol routing through the @dynamic vtable for
# both inheritance (Parrot inherits Pet) and structural (Dog conforms via
# Adapter) conformers. CPython-only excluded: Box's __deref__ doesn't
# auto-passthrough attribute access in CPython (would need __getattr__);
# see LANGUAGE_FEATURES.md note on Rc[T]/Box[T] CPython behavior.
from typing import Protocol
from tpy import dynamic
from tplib import Box


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Parrot(Pet):
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label


class Dog:
    label: str
    def __init__(self, label: str) -> None:
        self.label = label
    def name(self) -> str:
        return self.label


def main() -> None:
    b1: Box[Pet] = Box(Parrot(label="Polly"))   # inheritance
    b2: Box[Pet] = Box(Dog(label="Rex"))         # structural
    print(b1.name())                              # deref through inherited vtable
    print(b2.name())                              # deref through Adapter's vtable


main()
