# tpy: ext_module
# An exposed enum used as a getset FIELD of an exposed class. The field
# round-trips its member through the module enum type (enum_to_py reconstructs
# the singleton, enum_from_py is strict by-type), so reading preserves member
# identity and writing accepts only a real member. Enums are value types with
# interned singletons, so the getset copy is identity-preserving -- unlike a
# nested class field, which stays rejected because copy-out breaks write-through.
from enum import IntEnum
from tpy.extern import export


@export
class Color(IntEnum):
    RED = 1
    GREEN = 2
    BLUE = 3


@export
class Widget:
    def __init__(self, c: Color):
        self.color = c

    def set_color(self, c: Color) -> None:
        self.color = c

    def get_color(self) -> Color:
        return self.color
