from tpy import Int32, copy, auto_readonly

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
    @auto_readonly
    def __deref__(self) -> Point:
        return self._target

def main() -> None:
    pt: Point = Point(10, 20)
    r: Ref = Ref(pt)
    # Method call through user-defined __deref__
    print(r.sum())

main()
