from tpy import int32
class Circle:
    r: int32
    def __init__(self, r: int32) -> None:
        self.r = r
class Square:
    s: int32
    def __init__(self, s: int32) -> None:
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
