from tpy import Int32
class Circle:
    r: Int32
    def __init__(self, r: Int32) -> None:
        self.r = r
class Square:
    s: Int32
    def __init__(self, s: Int32) -> None:
        self.s = s
type Shape = Circle | Square
class Holder:
    _shapes: list[Shape]
    def __init__(self) -> None:
        self._shapes = [Circle(1)]
    @property
    def first(self) -> Shape:
        return self._shapes[0]
def main() -> None:
    h = Holder()
    print(1)
main()
