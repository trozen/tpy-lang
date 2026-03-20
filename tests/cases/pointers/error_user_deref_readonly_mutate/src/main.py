# Calling a mutable method through readonly user-defined deref is rejected
from tpy import Int32, copy, auto_readonly, readonly

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y
    def set_x(self, v: Int32) -> None:
        self.x = v

class Ref:
    _target: Point
    def __init__(self, target: Point) -> None:
        self._target = copy(target)
    @auto_readonly
    def __deref__(self) -> auto_readonly[Point]:
        return self._target

def bad(r: readonly[Ref]) -> None:
    r.set_x(99)  # tpyc: error(/non-readonly/)

def main() -> None:
    r = Ref(Point(1, 2))
    bad(r)

main()
