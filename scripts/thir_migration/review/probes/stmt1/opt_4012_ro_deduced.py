from tpy import Int32, Own, readonly
class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
class Holder:
    value: Point | None
    def __init__(self) -> None:
        self.value = None
def go(h: readonly[Holder]) -> Int32:
    v: Point | None = Point(1)
    if v is not None:
        print(v.x)
    v = h.value
    if v is not None:
        return v.x
    return 0
def main() -> None:
    print(go(Holder()))
main()
