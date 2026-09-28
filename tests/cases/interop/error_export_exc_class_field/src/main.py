# A data-carrying user exception with an exposed-class field can't cross the
# CPython boundary yet: marshalling a nested class instance to an attribute is
# deferred (like the exposed-class getset-field case).
# tpy: ext_module
from tpy import int64
from tpy.extern import export


@export
class Point:
    def __init__(self, x: int64):
        self.x = x


class ParseError(ValueError):
    where: Point  # tpyc: error(/data field 'where' of type Point cannot cross/)

    def __init__(self, message: str, where: Point):
        super().__init__(message)
        self.where = where


@export
def parse(n: int64) -> int64:
    return n
