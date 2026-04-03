# Property returning union of non-value types with isinstance narrowing
from tpy import Int32

class Circle:
    radius: Int32
    def __init__(self, r: Int32) -> None:
        self.radius = r

class Square:
    side: Int32
    def __init__(self, s: Int32) -> None:
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
