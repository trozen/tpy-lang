from tpy import int32, copy, auto_readonly, Own

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y
    def sum(self) -> int32:
        return self.x + self.y

class Ref:
    _target: Point
    def __init__(self, target: Point) -> None:
        self._target = copy(target)
    @auto_readonly
    def __deref__(self) -> Point:
        return self._target

class Holder[T]:
    v: T
    def __init__(self, v: Own[T]) -> None:
        self.v = v
    def __deref__(self) -> T:
        return self.v

class Sub[U](Holder[U]):
    pass

def inherited() -> None:
    s = Sub[Point](Point(1, 2))
    # Field read, write and method call through an inherited __deref__.
    print("inherited", s.x, s.sum())  # tpyc: ok
    s.x = 5  # tpyc: ok
    print("inherited-write", s.v.x, s.sum())
    ls = Sub[list[int32]]([1])
    # A container stub method through the inherited __deref__.
    ls.append(2)  # tpyc: ok
    print("inherited-stub", len(ls.v))

def main() -> None:
    pt: Point = Point(10, 20)
    r: Ref = Ref(pt)
    # Method call through user-defined __deref__
    print(r.sum())
    inherited()

main()
