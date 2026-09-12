# tpy: ext_module
# An exposed VALUE-type record used as a getset field of an exposed reference
# class. Value types are immutable, so the getset copy-out is behaviorally
# invisible: reading the field gives an equal value, the holder can replace the
# whole value, but the nested value's own fields are exposed READ-ONLY (a
# mutation attempt loud-fails with AttributeError -- see ext_checks). This is
# the safe bucket a reference-class field is NOT (that stays rejected).
from tpy import int64, ValueType
from tpy.extern import export


@export
class Point(ValueType):
    def __init__(self, x: int64, y: int64):
        self.x = x
        self.y = y

    def __eq__(self, other: "Point") -> bool:
        return self.x == other.x and self.y == other.y

    def __hash__(self) -> int64:
        return self.x * 31 + self.y


@export
class Box:
    def __init__(self, p: Point):
        self.origin = p

    def get_origin(self) -> Point:
        return self.origin
