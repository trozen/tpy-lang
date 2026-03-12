from tpy import Int32, copy, readonly_alt

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y
    def sum(self) -> Int32:
        return self.x + self.y

class Ref:
    _target: Point
    def __init__(self, target: Point) -> None:
        self._target = copy(target)
    @readonly_alt
    def __deref__(self) -> Point:
        return self._target

def main() -> None:
    # Narrowed Optional — flow analysis proves non-None
    r: Ref | None = Ref(Point(10, 20))
    print(r.x)
    print(r.sum())

main()
