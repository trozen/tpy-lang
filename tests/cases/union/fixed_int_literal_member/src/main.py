# A literal at a `fixedint | int` slot lands in the fixed-width member: the
# untyped literal follows the default-int rule, so `u: int32 | int = 5` holds
# an int32, and a value that does not fit the fixed width falls to `int`.
# Pins that the choice does not follow the union's canonical member order
# (which sorts `int` before `int32`). No CPython run: the cpy stub's `int32`
# is an `int` subclass, so `isinstance(5, int32)` is False there
# (BUGS.md#isinstance-int32-cpy-stub-subclass).
from tpy import int32, uint8


# free function: literal fits the fixed member
def fits() -> None:
    u: int32 | int = 5
    match u:
        case int32():
            print("fits int32")
        case int():
            print("fits int")


# free function: literal outside the fixed member's range falls to int
def overflows() -> None:
    u: uint8 | int = -1
    match u:
        case uint8():
            print("overflows uint8")
        case int():
            print("overflows int")


# field slot: same rule for a literal stored through a record field
class Holder:
    v: int32 | int

    def __init__(self) -> None:
        self.v = 9


def field_slot() -> None:
    h = Holder()
    match h.v:
        case int32():
            print("field int32")
        case int():
            print("field int")


def main() -> None:
    fits()
    overflows()
    field_slot()


main()
