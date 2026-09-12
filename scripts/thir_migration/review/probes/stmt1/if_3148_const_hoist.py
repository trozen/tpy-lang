from tpy import int32, Own, readonly
class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
class Reg:
    p: Point
    def __init__(self) -> None:
        self.p = Point(1)
    @readonly
    def view(self) -> readonly[Point]:
        return self.p
def pick(c: bool, r: Reg) -> int32:
    if c:
        v = r.view()
    else:
        v = r.view()
    return v.x
def main() -> None:
    print(pick(True, Reg()))
main()
