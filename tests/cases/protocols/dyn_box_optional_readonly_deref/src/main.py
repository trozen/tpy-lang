# Phase 20 Stage 1 prototype -- proves the composition chain that Phase 20's
# `Box[Throwable] | None` storage will rely on, using a synthetic @dynamic
# protocol so the test is independent of the real exception ABI.
#
# Chain under test:
#   Box[@dynamic Proto] | None field
#     -> narrow via `is not None` to Box[@dynamic Proto]
#     -> auto-deref through Box.__deref__ to @readonly virtual method
#     -> virtual dispatch picks the dynamic-type override
#
# Also covers rebinding the field to fresh-rvalue Box(Sub()) of different
# subclasses (mirrors TaskState.exc rebind across cancel/error/result paths).
from typing import Protocol
from tpy import dynamic, readonly
from tplib import Box


@dynamic
class Speakable(Protocol):
    @readonly
    def speak(self) -> str: ...


class Dog(Speakable):
    @readonly
    def speak(self) -> str:
        return "woof"


class Cat(Speakable):
    @readonly
    def speak(self) -> str:
        return "meow"


class Holder:
    val: Box[Speakable] | None

    def __init__(self) -> None:
        self.val = None

    def emit(self) -> None:
        if self.val is not None:
            print(self.val.speak())
        else:
            print("(empty)")


def main() -> None:
    h = Holder()
    h.emit()
    h.val = Box(Dog())
    h.emit()
    h.val = Box(Cat())
    h.emit()
    h.val = None
    h.emit()


main()
