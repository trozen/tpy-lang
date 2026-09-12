# User-defined __deref__ with @auto_readonly returns readonly[T]
# when receiver is readonly, propagating const through the deref chain
from tpy import int32, copy, auto_readonly, readonly

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y
    @readonly
    def sum(self) -> int32:
        return self.x + self.y
    def set_x(self, v: int32) -> None:
        self.x = v

class Ref:
    _target: Point
    def __init__(self, target: Point) -> None:
        self._target = copy(target)
    @auto_readonly
    def __deref__(self) -> auto_readonly[Point]:
        return self._target

def read_ref(r: readonly[Ref]) -> int32:
    # readonly receiver -> readonly __deref__ -> readonly Point
    # field access and readonly methods should work
    return r.x + r.y

def mutate_ref(r: Ref) -> None:
    # mutable receiver -> mutable __deref__ -> mutable Point
    r.set_x(99)

def main() -> None:
    r = Ref(Point(10, 20))
    print(read_ref(r))
    mutate_ref(r)
    print(read_ref(r))

main()
