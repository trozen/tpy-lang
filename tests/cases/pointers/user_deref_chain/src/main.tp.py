from tpy import Int32, copy

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
    def __deref__(self) -> Point:
        return self._target

class Box:
    _inner: Ref
    def __init__(self, inner: Ref) -> None:
        self._inner = copy(inner)
    def __deref__(self) -> Ref:
        return self._inner

def main() -> None:
    pt: Point = Point(10, 20)
    b: Box = Box(Ref(pt))
    # Multi-hop deref chain: Box -> Ref -> Point
    print(b.x)
    print(b.y)
    print(b.sum())

main()
