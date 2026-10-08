# Property returning union of non-value types with isinstance narrowing
# `describe` only prints, so it takes `const Canvas&`: the getter loans its
# receiver to the local instead of crediting a mutation at the decl, and the
# ptr-variant lift below follows it (`to_const_ptr_variant`).
from tpy import int32

class Circle:
    radius: int32
    def __init__(self, r: int32) -> None:
        self.radius = r

class Square:
    side: int32
    def __init__(self, s: int32) -> None:
        self.side = s

class Canvas:
    _shape: Circle | Square

    def __init__(self, s: Circle | Square) -> None:
        self._shape = s

    @property
    def shape(self) -> Circle | Square:
        return self._shape

def describe(c: Canvas) -> None:
    s = c.shape
    if isinstance(s, Circle):
        print(s.radius)
    elif isinstance(s, Square):
        print(s.side)

def main() -> None:
    describe(Canvas(Circle(5)))
    describe(Canvas(Square(10)))

main()
