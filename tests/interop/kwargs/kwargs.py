# tpy: ext_module
# Keyword-argument marshalling across the @export boundary: free functions and a
# class __init__ + methods all accept their params positionally OR by keyword
# (PyArg_ParseTupleAndKeywords). move() mutates through keyword args and the
# change is observed on the SAME object -- the reference-semantics proof for a
# class-boundary test, here driven by a keyword call.
from tpy import Int64
from tpy.extern import export


@export
def total(a: Int64, b: Int64, c: Int64) -> Int64:
    return a + b + c


@export
def label_of(value: Int64, name: str) -> str:
    return name


@export
class Vec:
    def __init__(self, x: Int64, y: Int64):
        self.x = x
        self.y = y

    def move(self, dx: Int64, dy: Int64) -> None:
        self.x += dx
        self.y += dy

    def dot(self, other_x: Int64, other_y: Int64) -> Int64:
        return self.x * other_x + self.y * other_y
