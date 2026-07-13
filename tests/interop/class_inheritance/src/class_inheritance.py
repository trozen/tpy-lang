# tpy: ext_module
# Inheritance between exposed classes: Circle(Shape) and Disc(Circle) cross as
# real CPython types with the base wired as tp_base -- isinstance/MRO,
# inherited getset fields, MRO method override, base-typed params borrowing a
# derived payload (write-through), ctor inheritance (Disc), and partial
# comparison-dunder sets resolved across the hierarchy (Circle's dispatcher
# delegates __eq__ to Shape's). Overriding describe() fires the pre-existing
# method-hiding warning (TPy dispatch is static inside the module); the driver
# only calls overrides from the Python side (MRO dispatch, matches CPython) --
# the static-dispatch shape is asserted ext-only in ext_checks.py.
from tpy import Int64
from tpy.extern import export


@export
class Shape:
    name: str

    def __init__(self, name: str):
        self.name = name

    def describe(self) -> str:
        return "shape " + self.name

    @property
    def label(self) -> str:
        return "shape:" + self.name

    def __eq__(self, other: "Shape") -> bool:
        return self.name == other.name

    def __hash__(self) -> Int64:
        return len(self.name)


@export
class Circle(Shape):
    radius: float

    def __init__(self, name: str, radius: float):
        super().__init__(name)
        self.radius = radius

    def area(self) -> float:
        return self.radius * self.radius * 3.0

    def describe(self) -> str:
        return "circle " + self.name

    def __lt__(self, other: "Circle") -> bool:
        return self.radius < other.radius


@export
class Disc(Circle):
    def spin(self) -> str:
        return "spinning " + self.name


@export
class BaseBox:
    width: Int64

    def __init__(self, width: Int64):
        self.width = width

    def w(self) -> Int64:
        return self.width


@export
class MidBox(BaseBox):
    def tag(self) -> str:
        return "mid"


@export
class LeafBox(MidBox):
    def tag2(self) -> str:
        return "leaf"


@export
def rename(s: Shape, name: str) -> None:
    s.name = name


@export
def describe_via_base(s: Shape) -> str:
    return s.describe()


@export
def as_shape(c: Circle) -> Shape:
    return c
