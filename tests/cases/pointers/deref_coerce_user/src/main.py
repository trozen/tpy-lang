from tpy import int32, auto_readonly

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

class Ref:
    _target: Point

    def __init__(self, target: Point) -> None:
        self._target = target

    @auto_readonly
    def __deref__(self) -> Point:
        return self._target

def print_point(p: Point) -> None:
    print(p.x, p.y)

def test() -> None:
    pt: Point = Point(10, 20)
    r: Ref = Ref(pt)
    # Ref has __deref__() -> Point, so it should auto-coerce to Point
    print_point(r)

test()
