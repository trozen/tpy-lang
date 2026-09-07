# A user-Deref method call off an Optional FIELD receiver with NO narrowing
# guard: the read carries the runtime None check, which the field render the
# call needs cannot carry. The guarded receiver is pinned by
# tests/cases/protocols/dyn_box_optional_readonly_deref.
from typing import Optional, Protocol
from tpy import dynamic
from tplib.box import Box


@dynamic
class Pet(Protocol):
    def speak(self) -> str: ...


class Dog(Pet):
    def speak(self) -> str:
        return "woof"


class Holder:
    val: Optional[Box[Pet]]

    def __init__(self) -> None:
        self.val = None

    def emit(self) -> None:
        print(self.val.speak())  # tpyc: error(/method\.marker\.deref\.recv_shape/)


def main() -> None:
    h = Holder()
    h.val = Box(Dog())
    h.emit()


main()
