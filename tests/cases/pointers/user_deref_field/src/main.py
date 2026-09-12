from tpy import int32, copy, auto_readonly

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

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
    # Field access through user-defined __deref__
    print(r.x)
    print(r.y)

main()
