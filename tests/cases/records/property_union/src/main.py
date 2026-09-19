# Property returning union of non-value types with isinstance narrowing
# `describe` takes `Canvas&`, not `const Canvas&`: reading a borrow off an
# accessor credits the receiver as mutated at the DECL rather than at the
# write, so a body that only prints still pays the mutable binding -- and the
# ptr-variant lift below follows it (`to_ptr_variant`, not the const twin).
# TODO.md, "A borrow DECLARATION is credited as a mutation of its source".
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
