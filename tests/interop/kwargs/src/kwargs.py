# tpy: ext_module
# Keyword-argument marshalling across the @export boundary: free functions and a
# class __init__ + methods all accept their params positionally OR by keyword
# (PyArg_ParseTupleAndKeywords). move() mutates through keyword args and the
# change is observed on the SAME object -- the reference-semantics proof for a
# class-boundary test, here driven by a keyword call.
from tpy import int64
from tpy.extern import export


@export
def total(a: int64, b: int64, c: int64) -> int64:
    return a + b + c


@export
def label_of(value: int64, name: str) -> str:
    return name


@export
class Vec:
    def __init__(self, x: int64, y: int64):
        self.x = x
        self.y = y

    def move(self, dx: int64, dy: int64) -> None:
        self.x += dx
        self.y += dy

    def dot(self, other_x: int64, other_y: int64) -> int64:
        return self.x * other_x + self.y * other_y
