# A property setter typed as a VALUE-type exposed class is also rejected
# (same located error as the reference-class setter): the property setter's
# param is an ownership transfer regardless of value-ness -- the Own auto-wrap
# runs before a user record's ValueType flag is known -- so the borrowed
# argument payload cannot feed it. Steer to a plain method.
# tpy: ext_module
from tpy import Int64, ValueType
from tpy.extern import export


@export
class Point(ValueType):
    def __init__(self, x: Int64):
        self.x = x


@export
class Holder:
    _origin: Point

    def __init__(self) -> None:
        self._origin = Point(0)

    @property
    def origin(self) -> Point:
        return self._origin

    @origin.setter
    def origin(self, p: Point) -> None:  # tpyc: error(/property 'origin' setter takes exposed class 'Point'.*use a plain method/)
        self._origin = p
