# A record FIELD read at a @dynamic protocol parameter of a native function:
# the dynamic slot takes the adapter machinery, not a bare member read.
from typing import Protocol

from tpy import Int32, StrView, dynamic
from tpy.extern import native


@dynamic
class Pet(Protocol):
    def noise(self) -> StrView: ...


class Dog:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def noise(self) -> StrView:
        return "woof"


@native("my_ns::speak")
def speak(p: Pet) -> None: ...


class Box:
    d: Dog

    def __init__(self) -> None:
        self.d = Dog(1)


def run(b: Box) -> None:
    # The argument is a field read at a @dynamic protocol slot.
    speak(b.d)  # tpyc: error(/expr\.call:call\.native_arg\.record_f1_slot/)


def main() -> None:
    run(Box())


main()
